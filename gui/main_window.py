# -*- coding: utf-8 -*-
"""Main window: source selection, file list, scan preview,
start process, log & progress bar.

All user-facing operations are wrapped in try/except so crashes
write a detailed traceback to both the GUI log widget AND the
file log (logs/mmf_*.log) — no more silent exits.

改进(v9)：
  - 支持可配置的全局字体大小（解决界面文字偏小问题）
  - 日志区域使用独立等宽字体大小设置
改进(v10 - 流水线优化)：
  - 引入后台 FilePreloader 线程，实现异步双缓冲预拉取
  - 在处理当前视频时，后台提前将下一个视频缓存至本地临时目录（支持 3 秒延迟交叠，防止抢占带宽）
  - 缓存命中时自动使用本地文件处理，消费完毕即刻自动清理，保障磁盘空间
改进(v11 - 健壮性优化)：
  - 引入原子性写入（.tmp 转发），避免前台读到“半截”未下载完的损坏视频
  - 线程安全封装 FilePreloader.current_idx 访问
  - 增加 MainWindow.closeEvent，在退出程序时优雅注销线程、强力清除磁盘缓存
  - 优化干跑(dry-run)状态下的文件大小列视觉反馈
"""
import os
import sys
import shutil
import time
import datetime
import traceback
import threading
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLineEdit, QPushButton, QCheckBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QProgressBar, QFileDialog, QMenu, QAction,
    QAbstractItemView, QLabel, QDialog, QMessageBox,
)
from PyQt5.QtCore import QThread, pyqtSignal, QUrl, Qt, QTimer
from PyQt5.QtGui import QFont, QColor, QIcon, QDragEnterEvent, QDropEvent, QDesktopServices

from core import pipeline, probe, config as config_mod, utils
from gui.widgets import LogWidget, fmt_plan
from gui.sys_widget import SysMonitorWidget
from gui import settings_dialog


def _log_path():
    """Return the current log file path from main.py's handler."""
    try:
        # main.py sets up _FILE_LOG on stderr; we read its path
        return getattr(sys.stderr, "_path", None) or "N/A"
    except Exception:
        return "N/A"


# ---------------------------------------------------------------------------
# Preloader: 后台异步预下载/缓存线程
# ---------------------------------------------------------------------------
class CacheManager:
    """
    本地缓存管理器（重设计 v11，v23.18 恢复）:

    - 在程序根目录建立 tmp/，按任务列表序号建子目录 tmp/1/、tmp/2/、tmp/3/…
    - 每个任务：**先**把对应视频整体缓存到 tmp/N/，**再**本地一次性抽离音轨/字幕，
      全程复用本地文件，不反复走网络（解决旧实现"读字幕时又下载一次"的浪费）。
    - 预缓存提前 2 个：任务 N 开始时，确保 N+1、N+2 均已缓存。
    - 滑动窗口清理：完成任务 N 后清理 N-2（保留当前 + 前 1 个）。
    - 磁盘感知：失败快照按时间排序，磁盘不足时删旧留新。
    """
    def __init__(self, files, cache_root, log_callback, skip_paths=None,
                 keep_temp=False):
        self.files = files
        self.cache_root = cache_root
        self.log_callback = log_callback
        self.ready = {}            # idx -> 本地缓存路径
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._current_idx = -1     # worker 当前正在处理的任务下标（-1 = 未启动）
        self._cached_up_to = -1   # 已经预拉取到的最高下标
        self._thread = None
        # v23.20: 跳过已完成的文件，不浪费预缓存
        self._skip_set = set(skip_paths or [])
        # v23.21: 保留 temp/ 子目录（OCR帧/音轨WAV）不清理
        self._keep_temp = keep_temp

    @property
    def current_idx(self):
        with self._lock:
            return self._current_idx

    @current_idx.setter
    def current_idx(self, val):
        with self._lock:
            self._current_idx = val

    def local_path(self, idx):
        """任务 idx(0-based) 对应的本地缓存文件路径。替换空格为下划线避免命令行工具解析问题。"""
        base = os.path.basename(self.files[idx])
        safe = base.replace(" ", "_")
        return os.path.join(self.cache_root, str(idx + 1), safe)

    def start(self):
        os.makedirs(self.cache_root, exist_ok=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _log(self, msg):
        if self.log_callback:
            self.log_callback(msg)

    def _preload_one(self, idx):
        """预拉取一个文件（含错误处理）。跳过已完成文件的缓存。"""
        if idx < 0 or idx >= len(self.files):
            return
        # v23.20: 跳过已完成的文件（断点续传场景，不浪费预缓存）
        if idx < len(self.files) and self.files[idx] in self._skip_set:
            return
        f = self.files[idx]
        local = self.local_path(idx)
        tmp = local + ".tmp"
        try:
            if os.path.exists(local):
                with self._lock:
                    self.ready[idx] = local
                return
            os.makedirs(os.path.dirname(local), exist_ok=True)
            with open(f, "rb") as fsrc, open(tmp, "wb") as fdst:
                while True:
                    if self._stop_event.is_set():
                        break
                    buf = fsrc.read(4 * 1024 * 1024)
                    if not buf:
                        break
                    fdst.write(buf)
            if os.path.exists(tmp):
                if os.path.exists(local):
                    os.remove(local)
                os.rename(tmp, local)
            with self._lock:
                self.ready[idx] = local
            self._log(f"[缓存] 任务{idx + 1} 已就绪: {os.path.basename(f)}")
        except Exception as e:
            self._log(f"[缓存] 任务{idx + 1} 失败: {e}")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    def _run(self):
        # 1) 启动时先预拉取第一个
        self._preload_one(0)
        if self._stop_event.is_set():
            return
        self._cached_up_to = 0

        # 2) 循环：worker 推进后，确保当前下标后 2 个都已缓存
        while not self._stop_event.is_set():
            with self._lock:
                curr = self._current_idx
            # 至少提前缓存 2 个：确保 curr+1, curr+2 就绪
            target_min = curr + 2
            if target_min >= len(self.files):
                break
            # 从当前已缓存的上限逐步拉到 target_min
            while self._cached_up_to < target_min and self._cached_up_to + 1 < len(self.files):
                nxt = self._cached_up_to + 1
                self._preload_one(nxt)
                self._cached_up_to = nxt
                if self._stop_event.is_set():
                    return
            # 错峰：等 worker 继续推进
            time.sleep(0.5)

    def _is_valid_media(self, path):
        """快速验证缓存文件是否为有效的 MKV/MP4（读取 magic bytes）。"""
        try:
            with open(path, "rb") as f:
                head = f.read(16)
            # MKV/WebM EBML 头: 0x1A45DFA3
            if head[:4] == b'\x1a\x45\xdf\xa3':
                return True
            # MP4 ftyp box: 00..00 18 66 74 79 70
            if b'ftyp' in head[4:12]:
                return True
            return False
        except Exception:
            return False

    def ensure(self, idx):
        """获取本地缓存路径。验证文件可用性，损坏时自动重缓存。

        流程：
          1. 后台已就绪 → 验证文件存在且非空
          2. 验证通过 → 返回路径
          3. 验证失败 → 删除损坏文件，前台同步重缓存
          4. 重缓存失败 → 返回 None（跳过任务）
        """
        # 1) 检查 ready 表
        with self._lock:
            ready_path = self.ready.get(idx)
        if ready_path:
            if os.path.isfile(ready_path) and os.path.getsize(ready_path) > 0 \
                    and self._is_valid_media(ready_path):
                return ready_path
            # v23.35: 缓存文件损坏（文件头非 MKV/MP4）→ 清除标记并重缓存
            self._log(f"[缓存] 任务{idx + 1} 缓存文件无效，准备重缓存",
                      "warn")
            with self._lock:
                self.ready.pop(idx, None)
            try:
                os.remove(ready_path)
            except Exception:
                pass

        # 2) 前台同步缓存
        local = self.local_path(idx)
        try:
            os.makedirs(os.path.dirname(local), exist_ok=True)
            shutil.copy2(self.files[idx], local)
            if os.path.getsize(local) <= 0:
                raise OSError("缓存文件为空")
            with self._lock:
                self.ready[idx] = local
            return local
        except Exception as e:
            self._log(f"[缓存] 任务{idx + 1} 缓存失败: {e}")
            try:
                os.remove(local)
            except Exception:
                pass
            return None

    def cleanup_before(self, idx):
        """完成任务 idx(0-based) 后，清理上一任务(tmp/(idx)) 的缓存目录。"""
        prev = os.path.join(self.cache_root, str(idx))
        if os.path.isdir(prev):
            try:
                shutil.rmtree(prev, ignore_errors=True)
                self._log(f"[缓存] 已清理上一任务目录: {prev}")
            except Exception:
                pass

    def cleanup_all(self):
        """停止后台线程并清理所有数字子目录和临时文件，保留 tmp/ 本身。"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        for name in os.listdir(self.cache_root):
            fp = os.path.join(self.cache_root, name)
            if os.path.isdir(fp) and name.isdigit():
                try:
                    shutil.rmtree(fp, ignore_errors=True)
                except Exception:
                    pass
        # 清理临时文件目录 tmp/temp/
        tempd = os.path.join(self.cache_root, "temp")
        if os.path.isdir(tempd):
            try:
                shutil.rmtree(tempd, ignore_errors=True)
                os.makedirs(tempd, exist_ok=True)
            except Exception:
                pass
        self._log("[缓存] 全部任务完成，已清理 tmp/ 子目录（保留 tmp/）")

    # ------------------------------------------------------------------
    # v23.18: 滑动窗口 + 磁盘感知快照清理
    # ------------------------------------------------------------------
    def cleanup_sliding(self, idx_0based):
        """完成任务 idx(0-based) 后，保留当前 + 前 1 个，清理更早的缓存目录。

        语义示例（1-based）：
          - 任务 1 完成：不清理（无更早的）
          - 任务 2 完成：不清理（只到 1）
          - 任务 3 完成：清理 tmp/1/
          - 任务 4 完成：清理 tmp/2/

        v23.21: 若 _keep_temp=True，只删除视频缓存文件，保留 temp/ 子目录（OCR帧/WAV）。
        """
        keep = {idx_0based, idx_0based - 1}
        for name in os.listdir(self.cache_root):
            if not name.isdigit():
                continue
            n = int(name) - 1  # 1-based → 0-based
            if n not in keep:
                td = os.path.join(self.cache_root, name)
                try:
                    if self._keep_temp:
                        # 只删视频缓存，保留 temp/（OCR帧、音轨WAV）
                        for entry in os.listdir(td):
                            fp = os.path.join(td, entry)
                            if entry != "temp":
                                if os.path.isdir(fp):
                                    shutil.rmtree(fp, ignore_errors=True)
                                else:
                                    os.remove(fp)
                    else:
                        shutil.rmtree(td, ignore_errors=True)
                except Exception:
                    pass

    def cleanup_failure_snapshots(self, min_free_gb=5):
        """磁盘不足时逐步清理失败快照目录（非数字目录），保留最新的一个。"""
        try:
            usage = shutil.disk_usage(self.cache_root)
            free_gb = usage.free / (1024 ** 3)
        except Exception:
            return
        if free_gb >= min_free_gb:
            return
        # 收集所有快照目录（非数字目录），按 mtime 排序
        snapshots = []
        for name in os.listdir(self.cache_root):
            d = os.path.join(self.cache_root, name)
            if os.path.isdir(d) and not name.isdigit():
                snapshots.append((os.path.getmtime(d), d))
        snapshots.sort()  # 最旧在前
        for _, d in snapshots[:-1]:  # 保留最新的
            try:
                shutil.rmtree(d, ignore_errors=True)
                self._log(f"[缓存] 磁盘不足({free_gb:.1f}GB)，已清理旧快照: {d}")
            except Exception:
                pass


# ---------------------------------------------------------------------------
# v22: 模块级辅助函数 — Worker.run 中判断 AI 是否全部失败
# ---------------------------------------------------------------------------
def _any_track_failed(tracks):
    """只要音轨或字幕中有一个识别失败（und/unknown/OCR失败）就跳过。"""
    return any(
        getattr(t, "detected_iso", "und") in ("und", "unknown")
        or getattr(t, "ocr_failed", False)
        for t in tracks if t.track_type in ("audio", "subtitle")
    )


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------
class Worker(QThread):
    log = pyqtSignal(str, str)
    progress = pyqtSignal(int, int)
    file_start = pyqtSignal(int)  # v22: 文件开始处理，发射行号
    file_done = pyqtSignal(int, str, str, str, str)  # (row, plan, status, level, out_path)
    finished = pyqtSignal()

    def __init__(self, files, results, cfg, mode, cfg_override=None,
                 skip_done_paths=None):
        super().__init__()
        self.files = files
        self.results = results
        self.cfg = cfg
        self.cfg_override = cfg_override or cfg
        self.mode = mode            # 'scan' | 'process'
        self.skip_done_paths = set(skip_done_paths or [])  # v23.16: 断点续传
        self._stop = False
        self.cache = None

    def stop(self):
        self._stop = True
        if self.cache:
            self.cache.cleanup_all()

    def _log(self, msg, level="info"):
        self.log.emit(msg, level)

    def _relocate_output(self, orig_path, out_path):
        """把缓存在 tmp/N/ 里的输出文件搬回原始位置，使用 namer 的输出名。"""
        if not out_path or not os.path.exists(out_path):
            return out_path
        # v23.27: 直接用 out_path 的 basename (namer 生成)，不再覆盖
        odir = os.path.dirname(orig_path)
        out_basename = os.path.basename(out_path)
        target = os.path.join(odir, out_basename)
        if target != out_path:
            try:
                if os.path.exists(target):
                    os.remove(target)
                shutil.move(out_path, target)
                self._log(f"[搬运] 输出已移到: {target}")
            except Exception as e:
                self._log(f"[搬运] 移动输出失败: {e}", "error")
                return out_path
        return target

    # ------------------------------------------------------------------
    # v23.15: 任务级临时目录滑动窗口清理
    # v23.18: 适配 tmp/N/ 架构 —— 快照功能直接操作当前任务的 tmp/{i+1}/ 目录
    # ------------------------------------------------------------------
    def _cleanup_dir_children(self, d):
        """删除目录 d 下的所有条目，但保留目录 d 本身。"""
        if not os.path.isdir(d):
            return
        for name in os.listdir(d):
            fp = os.path.join(d, name)
            try:
                if os.path.isdir(fp):
                    shutil.rmtree(fp, ignore_errors=True)
                else:
                    os.remove(fp)
            except Exception:
                pass

    def _post_task_cleanup(self, debug_mode=False, success=True, task_idx=None):
        """任务结束后的临时目录滑动窗口清理。

        v23.18 中滑动窗口由 CacheManager.cleanup_sliding 负责，
        本方法仅处理旧版 tmp/temp/ 残留 + 调试快照。
        task_idx (0-based)：失败+调试时将该任务的 tmp/{i+1}/ 移栽到 debug_last/。
        """
        from core import config as _cfg
        tmp_root = os.path.join(_cfg.app_root(), "tmp")
        temp_dir = os.path.join(tmp_root, "temp")
        debug_last = os.path.join(tmp_root, "debug_last")

        if success:
            # 成功/跳过：清理旧版 tmp/temp/ 残留；回收上一次失败快照
            self._cleanup_dir_children(temp_dir)
            if os.path.isdir(debug_last):
                try:
                    shutil.rmtree(debug_last, ignore_errors=True)
                except Exception:
                    pass
            return

        # 失败/异常
        if not debug_mode:
            self._cleanup_dir_children(temp_dir)
            return

        # 调试模式：保留本次失败产物为快照
        try:
            if os.path.isdir(debug_last):
                shutil.rmtree(debug_last, ignore_errors=True)
            # v23.18: 优先使用任务级目录 tmp/{i+1}/
            snap_src = None
            if task_idx is not None:
                snap_src = os.path.join(tmp_root, str(task_idx + 1))
            if snap_src and os.path.isdir(snap_src):
                os.makedirs(debug_last, exist_ok=True)
                shutil.move(snap_src, os.path.join(debug_last, str(task_idx + 1)))
                self._log(f"[调试] 已保留任务{task_idx + 1}快照到 tmp/debug_last/")
            elif os.path.isdir(temp_dir):
                # 兼容：无任务级目录时使用旧 tmp/temp/
                os.makedirs(debug_last, exist_ok=True)
                moved = False
                for name in os.listdir(temp_dir):
                    src = os.path.join(temp_dir, name)
                    dst = os.path.join(debug_last, name)
                    try:
                        shutil.move(src, dst)
                        moved = True
                    except Exception:
                        pass
                if moved:
                    self._log("[调试] 已保留失败任务中间产物到 tmp/debug_last/ 供排查")
        except Exception:
            pass
        os.makedirs(temp_dir, exist_ok=True)

    @staticmethod
    def _purge_stale_temp_on_start():
        """启动处理前清掉历史残留（tmp/temp/、tmp/debug_last/、tmp/N/），避免堆积。"""
        try:
            from core import config as _cfg
            tmp_root = os.path.join(_cfg.app_root(), "tmp")
            # v23.19: 也清理 v23.18 引入的 tmp/N/ 数字子目录，防止旧损坏缓存被误用
            for sub in os.listdir(tmp_root):
                d = os.path.join(tmp_root, sub)
                if os.path.isdir(d):
                    shutil.rmtree(d, ignore_errors=True)
            os.makedirs(os.path.join(tmp_root, "temp"), exist_ok=True)
        except Exception:
            pass

    def run(self):
        total = len(self.files)
        # v23.18: 恢复本地缓存架构 —— 预缓存整片到 tmp/N/，直读本地避免反复走 NAS
        from core import config as _cfg
        tmp_root = os.path.join(_cfg.app_root(), "tmp")
        # v23.20: 传入已完成集合，背景缓存线程跳过这些文件，不浪费硬盘
        # v23.21: 传入 keep_ocr_frames 配置，开启时滑动窗口保留 temp/ 子目录
        keep_temp = bool(self.cfg_override.get("keep_ocr_frames", False))
        # v23.15: 启动前先清掉历史残留（含 tmp/N/），再启动缓存线程
        self._purge_stale_temp_on_start()
        self.cache = CacheManager(self.files, tmp_root,
                                   lambda m: self._log(m, "cache"),
                                   skip_paths=self.skip_done_paths,
                                   keep_temp=keep_temp)
        self.cache.start()
        debug_mode = bool(self.cfg_override.get("debug_mode", False))

        for i, f in enumerate(self.files):
            if self._stop:
                self.file_done.emit(i, "", "已取消", "warn", "")
                break
            self.file_start.emit(i)
            # v23.36: 通知缓存线程当前任务下标，确保预缓存 N+1、N+2
            self.cache.current_idx = i
            success = True
            try:
                if self.mode == "scan":
                    # v23.17: 导入记录后扫描模式也跳过已完成的文件
                    if self.skip_done_paths and f in self.skip_done_paths:
                        self._log(f"跳过(已分析): {os.path.basename(f)}")
                        self.file_done.emit(
                            i, "", "已完成(自动跳过)", "ok", "")
                        continue
                    # v23.18: 使用本地缓存 + 任务级 temp 目录
                    local = self.cache.ensure(i)
                    src = local if local else f
                    task_tmp = os.path.join(tmp_root, str(i + 1), "temp")
                    os.makedirs(task_tmp, exist_ok=True)
                    self._log(f"分析: {f}")
                    tracks, _ = pipeline.analyze_file(
                        src, self.cfg, log=lambda m, l="info": self._log(m, l),
                        orig_path=f, temp_dir=task_tmp)
                    self.results[f] = tracks
                    # v22: 所有识别失败 → 跳过
                    if _any_track_failed(tracks):
                        for t in tracks:
                            t.action = "skip"
                            t.note = t.note or "轨道识别失败，跳过"
                        self.file_done.emit(i, "部分轨道识别失败，整体跳过", "已跳过", "warn", "")
                        continue
                    self.file_done.emit(i, fmt_plan(tracks), "已分析", "ok", "")
                else:
                    # v23.16: 导入记录后续传 —— 自动跳过已完成的文件
                    if self.skip_done_paths and f in self.skip_done_paths:
                        self.file_done.emit(
                            i, "", "已完成(自动跳过)", "ok", "")
                        continue
                    # v23.18: 使用本地缓存
                    local = self.cache.ensure(i)
                    src = local if local else f
                    self._log(f"处理: {f}")
                    run_cfg = dict(self.cfg_override)
                    run_cfg["output_overwrite"] = False
                    if f in self.results:
                        cached = self.results[f]
                        if all(getattr(t, "action", "keep") == "skip"
                               for t in cached if t.track_type in ("audio", "subtitle")):
                            self.file_done.emit(i, "轨道识别失败，已跳过", "已跳过", "warn", "")
                            continue
                        _last_pct = [0]
                        def _on_remux_progress(pct):
                            if pct - _last_pct[0] >= 10 or pct == 0:
                                self._log(f"  转封装进度: {pct}%", "info")
                                _last_pct[0] = pct
                        ok, out, msg, _ = pipeline.process_tracks(
                            src, cached, run_cfg,
                            log=lambda m, l="info": self._log(m, l),
                            progress_callback=_on_remux_progress)
                    else:
                        ok, out, msg, tracks = pipeline.process_file(
                            src, run_cfg,
                            log=lambda m, l="info": self._log(m, l))
                        self.results[f] = tracks
                    # v23.18: 输出在 tmp/N/ 中，搬回 NAS 原始目录
                    out_path = self._relocate_output(f, out or "")
                    plan = fmt_plan(self.results[f])
                    self.file_done.emit(
                        i, plan, "完成" if ok else f"失败: {msg}",
                        "ok" if ok else "error", out_path or "")

            except Exception as e:
                success = False
                tb = traceback.format_exc()
                try:
                    sys.stderr.write(
                        f"[Worker异常] 文件 {os.path.basename(f)}:\n{tb}\n")
                except Exception:
                    pass
                self._log(f"[异常] 文件 {os.path.basename(f)}:\n{tb}", "error")
                self.file_done.emit(i, "", f"异常: {e}", "error", "")
            finally:
                # v23.18: 滑动窗口清理 + 磁盘感知快照清理
                self.cache.cleanup_sliding(i)
                self.cache.cleanup_failure_snapshots(min_free_gb=5)
                # v23.15/v23.18: 旧版 tmp/temp/ 残留清理 + 失败快照
                self._post_task_cleanup(debug_mode, success, task_idx=i)

            self.progress.emit(i + 1, total)

        # 整个流程结束：彻底清理
        self._post_task_cleanup(debug_mode, success=True)
        self.cache.cleanup_all()
        self.finished.emit()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = config_mod.load()
        utils.set_ffmpeg_paths(self.cfg.get("ffmpeg_path"),
                               self.cfg.get("ffprobe_path"))
        utils.set_verbose_tools(self.cfg.get("verbose_tools", False))
        # 应用全局字体设置
        self._apply_global_font()
        # 设置窗口图标（emoji 🎞️，失败降级文字）
        self._on_init_window_icon()
        self.files = []
        self.results = {}
        self._track_data = {}
        self._completed = {}          # v23.16: path -> (status, level)，已成功完成的文件
        # v23.26: 表格自动滚动到当前任务
        self._last_scroll_interaction = time.time()
        self._auto_scroll_timer = QTimer(self)
        self._auto_scroll_timer.timeout.connect(self._auto_scroll_tick)
        self._auto_scroll_timer.setSingleShot(True)
        self._pending_scroll_row = -1
        self.worker = None
        self.worker_cfg_override = None
        self._saved_after_last_mod = True
        self._init_ui()
        self._post_init_log()

    def _apply_global_font(self):
        """根据配置设置 QApplication 全局字体（解决界面文字偏小问题）。"""
        try:
            font_family = self.cfg.get("gui_font_family") or ""
            font_size = int(self.cfg.get("gui_font_size", 10))
            app = QApplication.instance()
            if app:
                font = app.font()
                if font_family:
                    font.setFamily(font_family)
                if font_size >= 8:
                    font.setPointSize(font_size)
                app.setFont(font)
        except Exception:
            pass

    def _apply_column_proportions(self):
        """按默认百分比分配表格列宽（在 _fill_table 末尾和窗口 resize 时调用）。

        比例：文件38% / 原始轨道22% / 原始大小5% / 优化后大小5% / 计划动作25% / 状态5%
        """
        props = [0.38, 0.22, 0.05, 0.05, 0.25, 0.05]
        avail = self.table.viewport().width() - (self.table.columnCount() - 1)
        for col, p in enumerate(props):
            self.table.setColumnWidth(col, max(int(avail * p), 40))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'table') and self.table.columnCount() > 0:
            self._apply_column_proportions()

    def _on_init_window_icon(self):
        """v22: 使用手绘场记板 SVG 作为窗口图标。"""
        try:
            icon_path = os.path.join(config_mod.app_root(),
                                     "resources", "icons", "clapperboard.svg")
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
        except Exception:
            pass

    def _safe(self, label, fn, *args, **kw):
        """Run fn(*args, **kw); catch any exception, log traceback, show error."""
        try:
            return fn(*args, **kw)
        except Exception as e:
            tb = traceback.format_exc()
            self.log.log(f"[{label}] 异常:\n{tb}", "error")
            # Also write to stderr (goes to file log via main.py)
            sys.stderr.write(f"[{label}] EXCEPTION:\n{tb}\n")

    # ----------------------------- UI -----------------------------
    def _init_ui(self):
        self.setWindowTitle(f"电影音轨/字幕标签修复工具  (MP4→MKV)  {config_mod.APP_VERSION}")
        self.setAcceptDrops(True)

        menubar = self.menuBar()
        m_file = menubar.addMenu("文件")
        m_file.addAction(QAction("保存扫描记录(Ctrl+S)", self,
                                 triggered=lambda: self._safe("保存记录",
                                                             self.save_record)))
        m_file.addAction(QAction("导入扫描记录(Ctrl+O)", self,
                                 triggered=lambda: self._safe("导入记录",
                                                             self.load_record)))
        m_file.addSeparator()
        m_file.addAction(QAction("退出", self, triggered=self.close))
        m_set = menubar.addMenu("设置")
        m_set.addAction(QAction("首选项...", self,
                                triggered=lambda: self._safe("设置",
                                                            self.open_settings)))
        m_help = menubar.addMenu("帮助")
        m_help.addAction(QAction("打开日志目录", self,
                                 triggered=self.open_log_dir))
        m_help.addAction(QAction("清理旧日志…", self,
                                 triggered=lambda: self._safe("清理日志",
                                                              self.cleanup_logs)))
        m_help.addSeparator()
        m_help.addAction(QAction("打开说明(README)",
                                 self, triggered=self.open_readme))
        m_help.addAction(QAction("作者主页", self,
                                 triggered=lambda: QDesktopServices.openUrl(
                                     QUrl("https://www.zhihu.com/people/2br2"))))

        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)

        # Source selection
        src = QGroupBox("源（支持 UNC 网络路径，可直接拖入文件/文件夹）")
        sv = QVBoxLayout(src)
        h1 = QHBoxLayout()
        self.le_path = QLineEdit()
        self.le_path.setPlaceholderText(
            r"例如 \\NAS\Movies 或 D:\Movies 或 /volume1/Movies")
        h1.addWidget(self.le_path, 4)
        self.b_folder = QPushButton("浏览文件夹")
        self.b_file = QPushButton("浏览文件")
        h1.addWidget(self.b_folder)
        h1.addWidget(self.b_file)
        sv.addLayout(h1)
        h2 = QHBoxLayout()
        self.bx_recursive = QCheckBox("递归子目录")
        self.bx_recursive.setChecked(self.cfg.get("recursive", True))
        self.le_ext = QLineEdit(",".join(
            self.cfg.get("extensions", ["mp4", "mkv"])))
        self.b_collect = QPushButton("收集文件")
        h2.addWidget(self.bx_recursive)
        h2.addWidget(QLabel("扩展名:"))
        h2.addWidget(self.le_ext, 1)
        h2.addWidget(self.b_collect)
        h2.addStretch(1)
        sv.addLayout(h2)
        v.addWidget(src)

        # 系统资源监控（纯黑底横条，在源区和表格之间
        self.sys_mon = SysMonitorWidget()
        self.sys_mon.setFixedHeight(32)
        v.addWidget(self.sys_mon)

        # Table（v8：增加原始大小和优化后大小两列）
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["文件", "原始轨道", "原始大小(GB)", "优化后大小(GB)", "计划动作", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        # 大小列右对齐、固定宽度
        for col in (2, 3):
            self.table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMouseTracking(True)
        # v23.16: 右键菜单（删除选中行）
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
        # v23.26: 用户手动滚动时记录交互时间
        self.table.verticalScrollBar().valueChanged.connect(self._on_table_scrolled)
        v.addWidget(self.table, 3)

        # Controls + progress
        hc = QHBoxLayout()
        self.b_scan = QPushButton("扫描并预览")
        self.b_run = QPushButton("开始处理")
        self.b_save = QPushButton("保存记录")
        self.b_stop = QPushButton("停止当前")
        self.b_stop_all = QPushButton("全部停止")
        self.b_open = QPushButton("打开输出目录")
        self.b_keep_failed = QPushButton("仅保留有问题的")
        self.b_scan.setEnabled(False)
        self.b_run.setEnabled(False)
        self.b_save.setEnabled(False)
        self.b_keep_failed.setEnabled(False)
        hc.addWidget(self.b_scan)
        hc.addWidget(self.b_run)
        hc.addWidget(self.b_save)
        hc.addWidget(self.b_stop)
        hc.addWidget(self.b_stop_all)
        hc.addStretch(1)
        hc.addWidget(self.b_keep_failed)
        hc.addWidget(self.b_open)
        v.addLayout(hc)

        # v23.33: 扫描后自动开始处理
        self.cb_auto_process = QCheckBox("扫描后自动处理（不审核直接处理）")
        self.cb_auto_process.setChecked(False)

        self.bar = QProgressBar()
        self.bar.setValue(0)
        v.addWidget(self.bar)

        # Log widget (使用可配置的等宽字体)
        log_font_size = int(self.cfg.get("log_font_size", 9))
        self.log = LogWidget(font_size=log_font_size)
        v.addWidget(self.log, 2)

        # 底部状态栏：仅版本署名（居右，15px 右边距，"林大路"为蓝色可点击链接）
        credit_label = QLabel(
            'by 知乎@<a href="https://www.zhihu.com/people/2br2" '
            'style="color:#4fc3f7;text-decoration:none;">林大路</a>'
            f'  {config_mod.APP_VERSION}')
        credit_label.setOpenExternalLinks(True)
        credit_label.setContentsMargins(0, 0, 15, 0)
        self.statusBar().addPermanentWidget(credit_label)

        # Signals (all wrapped via _safe where needed)
        self.b_folder.clicked.connect(lambda: self._safe("浏览文件夹",
                                                         self.browse_folder))
        self.b_file.clicked.connect(lambda: self._safe("浏览文件",
                                                       self.browse_file))
        self.b_collect.clicked.connect(lambda: self._safe("收集文件",
                                                          self.collect))
        self.b_scan.clicked.connect(lambda: self._safe("扫描预览",
                                                       self.do_scan))
        self.b_run.clicked.connect(lambda: self._safe("开始处理",
                                                       self.do_process))
        self.b_save.clicked.connect(lambda: self._safe("保存记录",
                                                        self.save_record))
        self.b_stop.clicked.connect(self.do_stop_current)
        self.b_stop_all.clicked.connect(self.do_stop_all)
        self.b_open.clicked.connect(lambda: self._safe("打开输出目录",
                                                       self.open_output))
        self.b_keep_failed.clicked.connect(lambda: self._safe("仅保留有问题的",
                                                               self._keep_failed_only))

    def _post_init_log(self):
        self.log.log('就绪。请选择源路径后点击「收集文件」。', "info")
        lp = _log_path()
        self.log.log(f"日志文件: {lp}", "info")
        self.log.log("日志含阶段标签 [SYSTEM]/[PIPELINE]/[AI]/[TOOLS]，"
                     "可在该文件按标签过滤定位问题。", "info")
        mm = self.cfg.get("mkvmerge_path") or "mkvmerge(PATH)"
        ff = self.cfg.get("ffmpeg_path") or "ffmpeg(PATH)"
        self.log.log(f"mkvmerge: {mm}", "info")
        self.log.log(f"RapidOCR: OpenVINO（自动识别简繁英）", "info")
        self.log.log(f"ffmpeg: {ff}", "info")
        vt = "开启" if self.cfg.get("verbose_tools") else "关闭"
        self.log.log(f"第三方工具详细日志: {vt}", "info")

    # --------------------------- Drag & drop ---------------------------
    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent):
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p:
                self.add_path(p)

    # --------------------------- Source operations ---------------------------
    def browse_folder(self):
        start = self.le_path.text().strip() or ""
        p = QFileDialog.getExistingDirectory(self, "选择文件夹", start)
        if p:
            self.le_path.setText(p)
            self.collect()

    def browse_file(self):
        """浏览文件：多选，逐个追加到文件列表（不替换已有文件）。"""
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择文件（可多选）", self.le_path.text(),
            "视频 (*.mp4 *.mkv);;All (*.*)")
        if not files:
            return
        # 追加到现有列表（去重）
        existing = set(self.files)
        new = [f for f in files if f not in existing]
        if not new:
            self.log.log("所有选择的文件已在列表中", "info")
            return
        self.files.extend(new)
        self.log.log(f"已添加 {len(new)} 个文件，共 {len(self.files)} 个", "info")
        self.b_scan.setEnabled(True)
        self.b_run.setEnabled(True)
        self._fill_table()
        if self.le_path.text().strip():
            self.le_path.setText(files[0])

    def add_path(self, p):
        """拖入文件时追加到列表（不去重，不替换已有文件）。"""
        from pathlib import Path
        p = Path(p).as_posix()
        if p not in self.files:
            self.files.append(p)
            self.log.log(f"已添加: {os.path.basename(p)}", "info")
            self.b_scan.setEnabled(True)
            self.b_run.setEnabled(True)
            self._fill_table()

    def collect(self):
        src = self.le_path.text().strip()
        if not src:
            return
        exts = [e.strip().lstrip(".")
                for e in self.le_ext.text().split(",") if e.strip()]
        self.cfg["recursive"] = self.bx_recursive.isChecked()
        self.cfg["extensions"] = exts
        files = []
        try:
            files = pipeline.collect_files(
                src, recursive=self.bx_recursive.isChecked(), extensions=exts)
        except Exception as e:
            self.log.log(f"收集失败: {e}", "error")
            return
        self.files = files
        self.results.clear()
        self._fill_table()
        self.log.log(f"已收集 {len(files)} 个文件。", "ok")
        self.b_scan.setEnabled(bool(files))
        self.b_run.setEnabled(bool(files))

    def _fill_table(self):
        self.table.setRowCount(len(self.files))
        for i, f in enumerate(self.files):
            self.table.setItem(i, 0, QTableWidgetItem(os.path.basename(f)))
            try:
                tr = probe.probe_media(f)
                self._track_data[f] = tr
                # 原始轨道列：视频 + 音轨 + 字幕合并显示
                tracks = sorted(tr, key=lambda t: t.track_id)
                track_lines = []
                for t in tracks:
                    if t.track_type == "video":
                        codec = (t.codec or "").upper()
                        h = getattr(t, "height", 0) or 0
                        if h >= 2160:    res = "2160p"
                        elif h >= 1080:  res = "1080p"
                        elif h >= 720:   res = "720p"
                        elif h >= 480:   res = "480p"
                        else:            res = "?"
                        track_lines.append(f"🎬 #{t.track_id} {codec} {res}")
                    elif t.track_type == "audio":
                        lang = t.language_raw or "und"
                        codec = (t.codec or "").upper()
                        ch = f"{t.channels or '?'}ch"
                        track_lines.append(f"🎵 #{t.track_id} {codec} {ch} {lang}")
                    elif t.track_type == "subtitle":
                        desc = t.codec or ""
                        raw = t.language_raw or "und"
                        track_lines.append(f"📝 #{t.track_id} {desc} {raw}")
                if not track_lines:
                    item = QTableWidgetItem("— 无音轨/字幕 —")
                    item.setForeground(QColor("#888888"))
                    self.table.setItem(i, 1, item)
                else:
                    self.table.setItem(i, 1,
                        QTableWidgetItem("\n".join(track_lines)))

                # 自适应行高：基于字体度量动态计算，跟随字体/字号变化
                fm = self.table.fontMetrics()
                line_h = max(fm.lineSpacing(), 18) + 4
                row_height = max(line_h, len(track_lines) * line_h) + 8
                self.table.setRowHeight(i, row_height)

            except Exception as e:
                self.log.log(f"探测轨道失败 [{os.path.basename(f)}]: {e}",
                             "warn")
                self.table.setItem(i, 1, QTableWidgetItem("?"))

            # 原始文件大小（GB，两位小数）
            try:
                src_size = os.path.getsize(f)
                src_gb = f"{src_size / (1024**3):.2f}"
                it_src = QTableWidgetItem(src_gb)
                it_src.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, 2, it_src)
            except Exception:
                self.table.setItem(i, 2, QTableWidgetItem("-"))
            # 优化后大小（初始为空，处理完成后填充）
            it_dst = QTableWidgetItem("")
            it_dst.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(i, 3, it_dst)
            self.table.setItem(i, 4, QTableWidgetItem(""))
            self.table.setItem(i, 5, QTableWidgetItem("待处理"))


    # --------------------------- Processing ---------------------------
    def _start_worker(self, mode):
        if self.worker and self.worker.isRunning():
            self.log.log("已有任务在进行。", "warn")
            return
        self._last_mode = mode  # v23.33: 记录模式，供 _on_finished 判断自动处理
        self._task_start = datetime.datetime.now()
        self._task_src_bytes = 0  # 源文件总大小
        self._task_dst_bytes = 0  # 优化后总大小
        self._task_skipped = []   # v22: 因识别问题被跳过的文件路径
        self._net_before = _get_net_bytes()  # v22: 网卡流量基线
        # v22: 通知托盘状态变更
        _ipc_send_state("scanning" if mode == "scan" else "processing")
        label = "扫描预览" if mode == "scan" else "处理"
        self.log.log(f"任务开始: {self._task_start.strftime('%H:%M:%S')}", "info")
        cfg_override = getattr(self, 'worker_cfg_override', None)
        # v23.17: 扫描/处理模式均支持断点续传 —— 导入记录后自动跳过已完成
        # v23.24: 区分扫描完成(已分析)和处理完成(完成)，避免扫描后点处理全部跳过
        if mode == "process":
            skip = {f for f, (s, _) in self._completed.items()
                     if s and ("完成" in s) and ("已分析" not in s)}
        elif mode == "scan":
            skip = set(self._completed.keys())
        else:
            skip = None
        self.worker = Worker(self.files, self.results, self.cfg, mode,
                             cfg_override=cfg_override, skip_done_paths=skip)
        self.worker.log.connect(self.log.log)
        self.worker.progress.connect(self._on_progress)
        self.worker.file_start.connect(self._on_file_start)
        self.worker.file_done.connect(self._on_file_done)
        self.worker.finished.connect(self._on_finished)
        self.bar.setMaximum(len(self.files))
        self.bar.setValue(0)
        self.b_scan.setEnabled(False)
        self.b_run.setEnabled(False)
        self.b_save.setEnabled(True)
        label = "扫描预览" if mode == "scan" else "处理"
        self.log.log(f"=== 开始{label} ({len(self.files)} 个文件) ===", "info")
        self.worker.start()

    def do_scan(self):
        self._start_worker("scan")

    def do_process(self):
        # 执行前检查：是否有未保存的修改
        if self.results and not getattr(self, '_saved_after_last_mod', True):
            reply = QMessageBox.question(
                self, "未保存的修改",
                "当前扫描结果有未保存的手动修改，是否先保存？",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if reply == QMessageBox.Cancel:
                return
            if reply == QMessageBox.Save:
                self.save_record()
        # 直接开始处理（移除覆盖/备份弹窗）
        run_cfg = dict(self.cfg)
        self.worker_cfg_override = run_cfg
        self._start_worker("process")

    def do_stop_current(self):
        """停止当前正在处理的任务（当前文件完成后停下）。"""
        if self.worker:
            self.worker._stop = True
            self.log.log("已请求停止（当前任务完成后停下）。", "warn")

    def do_stop_all(self):
        """全部停止：立刻中止所有任务并清缓存。"""
        if self.worker:
            self.worker.stop()
            self.log.log("已请求全部停止，缓存已清除。", "warn")

    def _reload_config(self):
        """设置保存后即时刷新，无需重启。"""
        self.cfg = config_mod.load()
        utils.set_ffmpeg_paths(self.cfg.get("ffmpeg_path"),
                               self.cfg.get("ffprobe_path"))
        utils.set_verbose_tools(self.cfg.get("verbose_tools", False))
        self._apply_global_font()
        log_font_size = int(self.cfg.get("log_font_size", 9))
        if hasattr(self.log, 'set_log_font_size'):
            self.log.set_log_font_size(log_font_size)
        self.log.log("设置已刷新（即时生效）。", "ok")

    def _on_progress(self, cur, total):
        self.bar.setValue(cur)

    def _set_row_color(self, row, color):
        """设置整行背景色。有背景色时文字设为白色，否则保留默认文字色。"""
        if color is None:
            return
        for col in range(self.table.columnCount()):
            item = self.table.item(row, col)
            if item is None:
                item = QTableWidgetItem("")
                self.table.setItem(row, col, item)
            item.setBackground(color)
            item.setForeground(QColor("#ffffff"))

    # v23.26: 表格自动滚动到当前任务
    def _on_table_scrolled(self, value):
        """用户手动滚动 → 重置闲置计时，取消延迟跳转。"""
        self._last_scroll_interaction = time.time()
        self._auto_scroll_timer.stop()
        self._pending_scroll_row = -1

    def _auto_scroll_tick(self):
        """闲置超时 → 恢复自动滚动到延迟的行。"""
        if self._pending_scroll_row >= 0:
            row = self._pending_scroll_row
            self._pending_scroll_row = -1
            if row < self.table.rowCount():
                self.table.scrollToItem(self.table.item(row, 0),
                                        QAbstractItemView.PositionAtCenter)

    def _on_file_start(self, row):
        """文件开始处理 → 整行淡蓝 + 自动滚动。"""
        self._set_row_color(row, QColor("#1a5276"))
        # v23.26: 如果用户60秒未手动滚动，自动跳到当前行
        idle = time.time() - self._last_scroll_interaction
        if idle >= 60:
            self.table.scrollToItem(self.table.item(row, 0),
                                    QAbstractItemView.PositionAtCenter)
        else:
            # 用户正在浏览，延迟到60秒后再跳
            self._pending_scroll_row = row
            self._auto_scroll_timer.start(int((60 - idle) * 1000))

    @staticmethod
    def _all_detection_failed(tracks):
        """判断该文件所有音轨+字幕的 AI 识别是否全部失败。"""
        relevant = [t for t in tracks if t.track_type in ("audio", "subtitle")]
        if not relevant:
            return False
        return all(
            getattr(t, "detected_iso", "und") in ("und", "unknown")
            for t in relevant
        )

    def _on_file_done(self, row, plan, status, level, out_path):
        if 0 <= row < self.table.rowCount():
            if plan:
                self.table.setItem(row, 4, QTableWidgetItem(plan))
                # 计划动作为多行时调高本行
                nlines = plan.count("\n") + 1
                current = self.table.rowHeight(row)
                needed = max(nlines * 18, 22)
                if needed > current:
                    self.table.setRowHeight(row, needed)
            it = QTableWidgetItem(status)
            self.table.setItem(row, 5, it)
            
            # 优化后文件大小展示 + 累计统计
            if out_path and os.path.exists(out_path):
                try:
                    dst_size = os.path.getsize(out_path)
                    dst_gb = f"{dst_size / (1024**3):.2f}"
                    it_dst = QTableWidgetItem(dst_gb)
                    it_dst.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.table.setItem(row, 3, it_dst)
                    # 累计源文件和优化后总大小
                    if row < len(self.files):
                        src_path = self.files[row]
                        if os.path.exists(src_path):
                            self._task_src_bytes += os.path.getsize(src_path)
                    self._task_dst_bytes += dst_size
                except Exception:
                    pass

        # v22: 根据结果设置行背景色（覆盖处理中的淡蓝）+ 白字
        color_map = {"ok": "#1b5e20", "warn": "#7a5c00", "error": "#7a0000"}
        bg = color_map.get(level)
        if bg:
            self._set_row_color(row, QColor(bg))

        # v22: 记录跳过的文件，供 _on_finished 汇总提示
        if level == "warn" and "跳过" in (status or ""):
            if 0 <= row < len(self.files):
                self._task_skipped.append(self.files[row])

    def _on_finished(self):
        self._auto_scroll_timer.stop()
        self._pending_scroll_row = -1
        self.b_scan.setEnabled(bool(self.files))
        self.b_run.setEnabled(bool(self.files))
        self.b_keep_failed.setEnabled(bool(self.files))
        end = datetime.datetime.now()
        task_count = len(self.files)
        duration = ""
        if hasattr(self, '_task_start') and self._task_start:
            delta = end - self._task_start
            total_sec = int(delta.total_seconds())
            if total_sec >= 3600:
                duration = f"{total_sec//3600}时{total_sec%3600//60}分"
            elif total_sec >= 60:
                duration = f"{total_sec//60}分{total_sec%60}秒"
            else:
                duration = f"{total_sec}秒"
        msg = f"以上共计 {task_count} 个任务，耗时 {duration}"
        # 空间统计
        src_gb = self._task_src_bytes / (1024**3) if hasattr(self, '_task_src_bytes') and self._task_src_bytes else 0
        dst_gb = self._task_dst_bytes / (1024**3) if hasattr(self, '_task_dst_bytes') and self._task_dst_bytes else 0
        if src_gb > 0:
            saved = src_gb - dst_gb
            msg += f"\n源文件总计 {src_gb:.2f}GB，优化后 {dst_gb:.2f}GB"
            if saved >= 5:
                msg += f"，🎉 恭喜，已为你节省 {saved:.2f}GB 空间！"
            elif saved >= 1:
                msg += f"，不错，为你腾出了 {saved:.2f}GB 空间"
            elif saved > 0:
                msg += f"，这次仅腾出 {saved:.2f}GB 空间"
            else:
                msg += "（优化后空间未减少）"
        self.log.log(msg, "ok")

        # v23.33: 扫描后自动处理
        if hasattr(self, '_last_mode') and self._last_mode == "scan" \
                and self.cb_auto_process.isChecked() and self.files:
            self.log.log("扫描完成，自动开始处理...", "info")
            self.do_process()

        # v22: 网卡流量统计（蓝色显示做分隔线）
        net_after = _get_net_bytes()
        net_before = getattr(self, '_net_before', None)
        if net_before is not None and net_after is not None:
            rx = net_after[0] - net_before[0]
            tx = net_after[1] - net_before[1]
            def _fmt(b):
                if b < 0: return "—"
                if b < 1024: return f"{b}B"
                if b < 1024**2: return f"{b/1024:.1f}KB"
                if b < 1024**3: return f"{b/1024**2:.1f}MB"
                return f"{b/1024**3:.2f}GB"
            self.log.log(f"━━━ 网络流量: 读取 {_fmt(rx)} ↙ 写入 {_fmt(tx)} ↗ ━━━", "keep")

        # v22: 汇总跳过的文件
        skipped = getattr(self, '_task_skipped', [])
        if skipped:
            self.log.log(
                f"以下 {len(skipped)} 个文件因识别问题被跳过，"
                f"建议重新执行或单独处理：", "warn")
            for sf in skipped:
                self.log.log(f"  {sf}", "info")

        self.log.log(f"任务结束: {end.strftime('%H:%M:%S')}", "info")
        # v22: 通知托盘先显示"完成"对勾
        _ipc_send_state("done")
        # 1.5 秒后恢复待机（让 tray 有时间展示对勾）
        QTimer.singleShot(1500, lambda: _ipc_send_state("idle"))

        # v23.33: 微信推送（任务完成时通知）
        if self.cfg.get("wechat_push_enabled", False):
            self._push_wechat_notify(task_count, duration, src_gb, dst_gb,
                                     saved if src_gb > 0 else 0)

    # v23.33: 微信推送（客服消息，需用户先关注公众号）
    def _push_wechat_notify(self, task_count, duration, src_gb, dst_gb, saved_gb):
        """任务完成后调用微信客服消息接口推送通知。"""
        import requests
        appid = self.cfg.get("wechat_appid", "").strip()
        secret = self.cfg.get("wechat_appsecret", "").strip()
        openid = self.cfg.get("wechat_openid", "").strip()
        if not (appid and secret and openid):
            self.log.log("微信推送未配置完整 (AppID/AppSecret/OpenID)，已跳过", "warn")
            return
        try:
            # 1) 拿 access_token
            tok_resp = requests.get(
                "https://api.weixin.qq.com/cgi-bin/token",
                params={"grant_type": "client_credential",
                        "appid": appid, "secret": secret},
                timeout=10)
            tok_data = tok_resp.json()
            access_token = tok_data.get("access_token")
            if not access_token:
                err = tok_data.get("errcode", "?")
                msg = tok_data.get("errmsg", "未知错误")
                self.log.log(f"微信推送获取 token 失败 [{err}] {msg}", "warn")
                return
            # 2) 发客服消息（48h 窗口内有效）
            text = (
                f"🎬 mkvtrackfix 任务完成！\n"
                f"\n"
                f"· 任务数: {task_count} 个\n"
                f"· 耗时:   {duration or '—'}\n"
            )
            if saved_gb >= 1:
                text += f"· 节省:   {saved_gb:.2f}GB 空间 💾\n"
            text += "\n—— 野生实验室 mkvtrackfix"
            resp = requests.post(
                f"https://api.weixin.qq.com/cgi-bin/message/custom/send?access_token={access_token}",
                json={
                    "touser": openid,
                    "msgtype": "text",
                    "text": {"content": text},
                },
                timeout=10)
            data = resp.json()
            if data.get("errcode") == 0:
                self.log.log("微信推送已发送 ✓", "ok")
            else:
                self.log.log(f"微信推送失败 [{data.get('errcode')}] {data.get('errmsg')}", "warn")
        except Exception as e:
            self.log.log(f"微信推送异常: {e}", "warn")

    # --------------------------- 行删除（v23.16） ---------------------------
    def _on_table_context_menu(self, pos):
        """表格右键菜单：删除选中行。处理进行中禁用删除。"""
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        if not rows:
            return
        menu = QMenu(self)
        has_worker = bool(self.worker and self.worker.isRunning())
        del_act = QAction(f"删除选中行 ({len(rows)})", self)
        del_act.triggered.connect(lambda: self._delete_rows(rows))
        if has_worker:
            del_act.setEnabled(False)
            del_act.setToolTip("处理进行中不可删除，请先「停止当前」")
        menu.addAction(del_act)
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def _delete_rows(self, rows):
        """从 files/results/_track_data/_completed 中同步删除选中行并重填表。"""
        rows = sorted(set(rows), reverse=True)
        removed = []
        for r in rows:
            if 0 <= r < len(self.files):
                f = self.files[r]
                removed.append(f)
                self.files.pop(r)
                self.results.pop(f, None)
                self._track_data.pop(f, None)
                self._completed.pop(f, None)
        if not removed:
            return
        self._fill_table()
        self.b_scan.setEnabled(bool(self.files))
        self.b_run.setEnabled(bool(self.files))
        self.log.log(
            f"已删除 {len(removed)} 个文件："
            + ", ".join(os.path.basename(x) for x in removed), "warn")

    # v23.32: 仅保留有问题的文件（失败/异常/已跳过），移除成功的
    def _keep_failed_only(self):
        """清掉成功/已完成的文件，只保留需要重试的。"""
        if not self.files:
            return
        keep = []
        removed = []
        for i, f in enumerate(self.files):
            status = ""
            if 0 <= i < self.table.rowCount():
                it = self.table.item(i, 5)
                if it:
                    status = it.text()
            # 有问题的：失败、异常、已跳过、无状态
            is_problem = any(k in status for k in ("失败", "异常", "跳过"))
            is_ok = any(k in status for k in ("已分析", "完成")) and "失败" not in status
            if is_problem or (not status or status == "待处理"):
                keep.append(f)
            else:
                removed.append(f)
                self.results.pop(f, None)
                self._track_data.pop(f, None)
                self._completed.pop(f, None)
        if not removed:
            self.log.log("没有需要移除的成功文件。", "info")
            return
        self.files = keep
        self._fill_table()
        self.b_scan.setEnabled(bool(self.files))
        self.b_run.setEnabled(bool(self.files))
        self.b_keep_failed.setEnabled(bool(self.files))
        self.log.log(
            f"已移除 {len(removed)} 个成功文件，保留 {len(keep)} 个需重试",
            "warn" if keep else "ok")

    # --------------------------- Other ---------------------------
    def open_settings(self):
        dlg = settings_dialog.SettingsDialog(self.cfg, self)
        if dlg.exec_() == QDialog.Accepted:
            self._reload_config()

    def save_record(self):
        """v23.16: 保存扫描/处理记录，含每行状态与已完成标记，支持断点续传。"""
        if not self.files:
            self.log.log("没有文件可保存。", "warn")
            return
        import json
        records_dir = os.path.join(config_mod.app_root(), "records")
        os.makedirs(records_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        fpath, _ = QFileDialog.getSaveFileName(
            self, "保存扫描/处理记录",
            os.path.join(records_dir, f"mmf_{timestamp}.json"),
            "扫描记录 (*.json);;所有文件 (*.*)")
        if not fpath:
            return
        data = {
            "version": 2,
            "timestamp": timestamp,
            "files": [],
        }
        completed_now = {}
        for i, f in enumerate(self.files):
            # 读表格当前状态列（5 列）+ 计划动作列（4 列）
            status = ""
            plan = ""
            if 0 <= i < self.table.rowCount():
                it5 = self.table.item(i, 5)
                it4 = self.table.item(i, 4)
                if it5:
                    status = it5.text()
                if it4:
                    plan = it4.text()
            tracks = self.results.get(f, [])
            tracks_data = []
            for t in tracks:
                if t.track_type in ("audio", "subtitle"):
                    tracks_data.append({
                        "id": t.track_id,
                        "type": t.track_type,
                        "detected_iso": t.detected_iso,
                        "detected_kind": getattr(t, "detected_kind", ""),
                        "action": t.action,
                        "name": t.track_name or t.detected_name or "",
                        "note": getattr(t, "note", "") or "",
                    })
            # 已完成：扫描「已分析」或处理「完成」且非失败
            done = (("完成" in status) or ("已分析" in status)) and ("失败" not in status)
            data["files"].append({
                "path": f,
                "status": status,
                "done": bool(done),
                "plan": plan,
                "tracks": tracks_data,
            })
            if done:
                completed_now[f] = (status, "ok")
        try:
            with open(fpath, "w", encoding="utf-8") as fo:
                json.dump(data, fo, ensure_ascii=False, indent=2)
            self._completed = completed_now
            self.log.log(f"记录已保存({len(data['files'])} 个文件, "
                         f"{len(completed_now)} 个已完成): {fpath}", "ok")
            self._saved_after_last_mod = True
        except Exception as e:
            self.log.log(f"保存失败: {e}", "error")

    def load_record(self):
        """v23.16: 从记录导入，恢复人工修改 + 状态列 + 已完成标记（断点续传）。"""
        import json
        records_dir = os.path.join(config_mod.app_root(), "records")
        fpath, _ = QFileDialog.getOpenFileName(
            self, "导入扫描记录", records_dir,
            "扫描记录 (*.json);;所有文件 (*.*)")
        if not fpath:
            return
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self.log.log(f"导入失败: {e}", "error")
            return
        from core.probe import Track
        loaded_files = []
        loaded_results = {}
        done_map = {}          # path -> (status, level)
        saved_status = {}      # path -> 保存时的状态文字
        for fe in data.get("files", []):
            orig = fe["path"]
            loaded_files.append(orig)
            tracks = []
            for te in fe.get("tracks", []):
                t = Track(stream_index=te["id"], track_id=te["id"],
                          track_type=te["type"], codec="")
                t.detected_iso = te.get("detected_iso")
                t.detected_kind = te.get("detected_kind")
                t.action = te.get("action", "keep")
                t.track_name = te.get("name", "")
                t.detected_name = te.get("name", "")
                t.note = te.get("note", "")
                tracks.append(t)
            loaded_results[orig] = tracks
            saved_status[orig] = fe.get("status", "")
            # v23.19: 兼容老记录——若 done 未设但 status 是「已分析」也视为完成
            is_done = fe.get("done") or ("已分析" in fe.get("status", ""))
            if is_done:
                done_map[orig] = (fe.get("status", "已完成"), "ok")
        self.files = loaded_files
        self.results = loaded_results
        self._track_data = {}
        self._completed = dict(done_map)
        self._fill_table()
        # 导入后恢复计划动作列 + 状态列 + 已完成绿色标记
        pol = None
        for i, f in enumerate(self.files):
            if f in self.results:
                if pol is None:
                    from core import policy as _pol
                    pol = _pol
                pol.apply_audio_policy(self.results[f], self.cfg)
                pol.apply_subtitle_policy(self.results[f], self.cfg)
                plan = fmt_plan(self.results[f])
                self.table.setItem(i, 4, QTableWidgetItem(plan))
            st = saved_status.get(f, "")
            if st:
                self.table.setItem(i, 5, QTableWidgetItem(st))
                if f in self._completed:
                    self._set_row_color(i, QColor("#1b5e20"))
        self.b_scan.setEnabled(True)
        self.b_run.setEnabled(True)
        self.b_save.setEnabled(True)
        self.b_keep_failed.setEnabled(True)
        n_done = len(self._completed)
        tip = f"（其中 {n_done} 个已完成，开始处理时将自动跳过）" if n_done else ""
        self.log.log(f"已导入记录: {fpath} ({len(loaded_files)} 个文件){tip}", "ok")
        self._saved_after_last_mod = True

    def open_readme(self):
        path = os.path.join(os.path.dirname(__file__), "..", "README.md")
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path)))

    def open_output(self):
        if self.files:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(os.path.dirname(
                    os.path.abspath(self.files[0]))))

    def open_log_dir(self):
        """Open the logs directory in Explorer."""
        try:
            log_dir = getattr(sys.stderr, "_dir", None)
            if not log_dir:
                # fallback: find logs/ relative to app
                if hasattr(config_mod, "app_root"):
                    log_dir = os.path.join(config_mod.app_root(), "logs")
                else:
                    log_dir = os.path.join(
                        os.path.dirname(__file__), "..", "logs")
            if log_dir and os.path.isdir(log_dir):
                QDesktopServices.openUrl(
                    QUrl.fromLocalFile(os.path.abspath(log_dir)))
            else:
                self.log.log("日志目录不存在。", "warn")
        except Exception as e:
            self.log.log(f"打开日志目录失败: {e}", "warn")

    def cleanup_logs(self):
        """清理 logs/ 目录下的所有 .log 文件，弹确认和结果框。"""
        log_dir = getattr(sys.stderr, "_dir", None)
        if not log_dir:
            log_dir = os.path.join(config_mod.app_root(), "logs")
        if not os.path.isdir(log_dir):
            QMessageBox.information(self, "清理日志", "日志目录不存在。")
            return
        logs = [f for f in os.listdir(log_dir)
                if f.startswith("mmf_") and f.endswith(".log")]
        if not logs:
            QMessageBox.information(self, "清理日志", "没有可清理的日志文件。")
            return
        total_size = sum(os.path.getsize(os.path.join(log_dir, f))
                         for f in logs if os.path.isfile(os.path.join(log_dir, f)))
        size_s = f"{total_size / 1024:.0f} KB" if total_size < 1024**2 \
                 else f"{total_size / 1024**2:.2f} MB"
        reply = QMessageBox.question(
            self, "清理旧日志",
            f"将删除 {len(logs)} 个日志文件（共 {size_s}），确定？",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        deleted = 0
        for f in logs:
            fp = os.path.join(log_dir, f)
            try:
                if os.path.isfile(fp):
                    os.remove(fp)
                    deleted += 1
            except Exception:
                pass
        QMessageBox.information(
            self, "清理完成",
            f"已清理 {deleted}/{len(logs)} 个日志文件。")

    def closeEvent(self, event):
        """主窗口关闭时触发：注销 Worker 线程及后台预下载线程，并清扫缓存目录"""
        try:
            if self.worker:
                self.log.log("正在强行结束后台线程并清理临时磁盘文件...", "warn")
                self.worker.stop()
                self.worker.wait(3000)  # 最多等 3 秒让其释放文件句柄并退出
            
            # 物理清理缓存子目录（保留 tmp/ 本身）
            from core import config as cfg_mod
            cache_root = os.path.join(cfg_mod.app_root(), "tmp")
            if os.path.isdir(cache_root):
                for name in os.listdir(cache_root):
                    fp = os.path.join(cache_root, name)
                    if os.path.isdir(fp) and (name.isdigit() or name == "temp"
                                             or name == "debug_last"):
                        try:
                            shutil.rmtree(fp)
                        except Exception:
                            pass
        except Exception:
            pass
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("MediaMetaFixer")
    w = MainWindow()
    w.show()
    # 在 show() 之后调 showMaximized() 才生效（之前在 _init_ui 里调会被忽略）
    w.showMaximized()
    sys.exit(app.exec_())


# v22: IPC 通知托盘进程状态切换
def _ipc_send_state(state):
    """写入状态文件供 tray_monitor 轮询读取。"""
    try:
        status_dir = os.path.join(config_mod.app_root(), "tmp")
        os.makedirs(status_dir, exist_ok=True)
        with open(os.path.join(status_dir, "tray_status.txt"), "w", encoding="utf-8") as f:
            f.write(state)
    except Exception:
        pass


def _get_net_bytes():
    """获取网卡累计收发字节数 (rx_bytes, tx_bytes)，失败返回 None。"""
    try:
        import psutil
        io = psutil.net_io_counters()
        return (io.bytes_recv, io.bytes_sent)
    except Exception:
        return None


if __name__ == "__main__":
    main()