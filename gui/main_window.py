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


def _format_duration(seconds):
    """将秒数格式化为可读时间。>=60秒输出分秒。"""
    if seconds >= 60:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}分{s}秒"
    return f"{int(seconds)}秒"


def _is_unc_path(path):
    """判断路径是否为 UNC 网络路径（以 \\\\ 或 // 开头）。"""
    return path.startswith("\\\\") or path.startswith("//")


# ---------------------------------------------------------------------------
# Preloader: 后台异步预下载/缓存线程
# ---------------------------------------------------------------------------
class CacheManager:
    """
    本地缓存管理器（v23 滑动窗口重设计）：

    - 仅在路径为 UNC 网络路径时启用（自动判度）
    - 在程序根目录建立 tmp/，按任务列表序号建子目录 tmp/1/、tmp/2/…
    - 滑动窗口预缓存：始终预拉取「当前任务 + 2 个向前」（WINDOW=3）
      worker 推进到任务 N 时，后台线程目标推进到 N+2，任务 N+3 不再预取
      由 current_idx 作为唯一权威，杜绝「预取的目录被误清」竞态
    - 等待机制：Worker 等待指定视频缓存就绪，输出等待时间
    - 清理（滑窗）：任务 N 完成后，清理 idx < current_idx - (WINDOW-1) 的目录，
      即至少清理到 N-2（含 N-2），仅保留 N-1、N 两个目录；
      清理边界(<=N-2)与预取目标(>=N)间隔充分，正处理/正在预取的目录绝不误删
    - 调试模式下清理只删 temp/ 子目录，保留缓存视频便于排查
    """
    WINDOW = 3  # 滑动窗口：始终预缓存「当前 + 2 个向前」（共 3 个）

    def __init__(self, files, cache_root, log_callback):
        self.files = files
        self.cache_root = cache_root
        self.log_callback = log_callback
        self.ready = {}            # idx -> 本地缓存路径
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._current_idx = -1     # worker 当前正在处理的任务下标（-1 = 未启动）
        self._preload_idx = -1     # 后台线程已预拉取的最高下标
        self._thread = None

        # 检查是否有 UNC 路径需要缓存
        self.has_unc = any(_is_unc_path(f) for f in files)

    @property
    def current_idx(self):
        with self._lock:
            return self._current_idx

    @current_idx.setter
    def current_idx(self, val):
        with self._lock:
            self._current_idx = val

    def local_path(self, idx):
        """任务 idx(0-based) 对应的本地缓存文件路径。"""
        return os.path.join(self.cache_root, str(idx + 1),
                            os.path.basename(self.files[idx]))

    def task_temp_dir(self, idx):
        """任务 idx 对应的临时工作目录（temp 子目录）。"""
        return os.path.join(self.cache_root, str(idx + 1), "temp")

    def start(self):
        if not self.has_unc:
            return  # 本地路径，无需缓存
        os.makedirs(self.cache_root, exist_ok=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _log(self, msg):
        if self.log_callback:
            self.log_callback(msg)

    def _preload_one(self, idx):
        """预拉取一个文件。"""
        if idx < 0 or idx >= len(self.files):
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
        # 1) 启动时先缓存第一批（最多 WINDOW 个）
        n = min(self.WINDOW, len(self.files))
        for idx in range(n):
            self._preload_one(idx)
            if self._stop_event.is_set():
                return
        self._preload_idx = n - 1

        # 2) 循环：以 current_idx 为唯一权威，维护滑动窗口 = WINDOW
        #    目标预取下标 = curr + WINDOW - 1（当前 + 2 向前）
        while not self._stop_event.is_set():
            with self._lock:
                curr = self._current_idx
            if curr < 0:
                time.sleep(0.2)
                continue
            target = curr + self.WINDOW - 1
            if target >= len(self.files):
                break
            with self._lock:
                if target <= self._preload_idx:
                    time.sleep(0.2)
                    continue
            # 错峰：预拉取前等 3 秒，避免与当前任务抢带宽
            for _ in range(30):
                if self._stop_event.is_set():
                    break
                time.sleep(0.1)
            if self._stop_event.is_set():
                break
            self._preload_one(target)
            self._preload_idx = target

    def wait_until_ready(self, idx):
        """阻塞等待 idx 缓存就绪，输出等待时间。返回本地缓存路径。"""
        # 非 UNC 路径直接返回原始路径
        if not self.has_unc:
            return self.files[idx]

        # 快速路径：已就绪
        with self._lock:
            if idx in self.ready:
                return self.ready[idx]

        start = time.time()
        self._log(f"[缓存] 等待任务{idx + 1} 缓存就绪...")
        while not self._stop_event.is_set():
            with self._lock:
                if idx in self.ready:
                    elapsed = time.time() - start
                    if elapsed >= 1:
                        self._log(f"[缓存] 任务{idx + 1} 已就绪（等待{_format_duration(elapsed)}）")
                    return self.ready[idx]
            time.sleep(0.1)

        # 被终止
        return None

    def mark_processing(self, idx):
        """Worker 真正开始处理任务 idx 前调用，加锁置 current_idx。

        后台预取线程据此把预取目标推进到 idx + WINDOW - 1，
        当前窗口外的目录才允许清理，杜绝误删正在预取的目录。
        """
        with self._lock:
            self._current_idx = idx

    def on_task_done(self, idx, debug_mode=False):
        """任务 idx(0-based) 完成后调用：
        1) 加锁推进 current_idx = idx + 1（通知后台线程滑窗前进）
        2) 滑窗清理：删除所有 d_idx < current_idx - (WINDOW - 1) 的数字目录
           即处理任务 N 完成时至少清理到 N-2（含 N-2），仅保留 N-1、N
           两个目录；预取目标始终在 curr + WINDOW - 1（= N+2）之外，
           清理边界(<=N-2)与预取目标(>=N)间隔 >=4，绝无误删竞态

        参数：
          debug_mode — 调试模式时仅清理 temp/ 子目录，保留缓存视频文件
        """
        if not self.has_unc:
            return
        with self._lock:
            self._current_idx = idx + 1
            cleanup_threshold = self._current_idx - (self.WINDOW - 1)
            for name in os.listdir(self.cache_root):
                if not (name.isdigit()):
                    continue
                d_idx = int(name) - 1
                if d_idx >= cleanup_threshold:
                    continue  # 仍在窗口内（N-1、N），保留
                target = os.path.join(self.cache_root, name)
                if not os.path.isdir(target):
                    continue
                if debug_mode:
                    # 调试模式：只清 temp 子目录，保留缓存视频
                    temp_dir = os.path.join(target, "temp")
                    if os.path.isdir(temp_dir):
                        try:
                            shutil.rmtree(temp_dir, ignore_errors=True)
                            self._log(f"[缓存] 调试模式，已清理临时目录: {temp_dir}")
                        except Exception:
                            pass
                else:
                    try:
                        shutil.rmtree(target, ignore_errors=True)
                        self._log(f"[缓存] 已清理任务{name} 目录: {target}")
                    except Exception:
                        pass

    def cleanup_all(self, debug_mode=False):
        """停止后台线程并清理所有数字子目录，保留 tmp/ 本身。

        参数：
          debug_mode — 调试模式时跳过清理（保留所有调试文件）
        """
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self.has_unc and not debug_mode:
            for name in os.listdir(self.cache_root):
                fp = os.path.join(self.cache_root, name)
                if os.path.isdir(fp) and name.isdigit():
                    try:
                        shutil.rmtree(fp, ignore_errors=True)
                    except Exception:
                        pass
            self._log("[缓存] 全部任务完成，已清理 tmp/ 子目录（保留 tmp/）")
        elif debug_mode:
            self._log("[缓存] 调试模式，保留所有临时文件")


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

    def __init__(self, files, results, cfg, mode, cfg_override=None):
        super().__init__()
        self.files = files
        self.results = results
        self.cfg = cfg
        self.cfg_override = cfg_override or cfg
        self.mode = mode            # 'scan' | 'process'
        self._stop = False
        self.cache = None

    def stop(self):
        self._stop = True
        if self.cache:
            self.cache.cleanup_all()

    def _log(self, msg, level="info"):
        self.log.emit(msg, level)

    def _relocate_output(self, orig_path, out_path):
        """把缓存在 tmp/N/ 里的输出文件搬回原始位置（覆盖/后缀模式）。"""
        if not out_path or not os.path.exists(out_path):
            return out_path
        overwrite = self.cfg_override.get("output_overwrite", False)
        keep_backup = self.cfg_override.get("keep_backup", False)
        suffix = self.cfg_override.get("output_suffix", ".fixed")
        odir = os.path.dirname(orig_path)
        obase = os.path.basename(orig_path)
        onext = os.path.splitext(obase)[1].lower()

        if overwrite:
            target = orig_path
            if keep_backup and os.path.exists(orig_path):
                try:
                    bak = orig_path + ".bak"
                    if os.path.exists(bak):
                        os.remove(bak)
                    os.replace(orig_path, bak)
                except OSError as e:
                    self._log(f"[搬运] 备份原文件失败: {e}", "warn")
        elif onext in (".mp4", ".m4v"):
            target = os.path.join(odir, os.path.splitext(obase)[0] + ".mkv")
        else:
            target = os.path.join(odir, os.path.splitext(obase)[0] + suffix + ".mkv")

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

    def run(self):
        total = len(self.files)
        cache_root = os.path.join(config_mod.app_root(), "tmp")

        # v23: 自动判度 — UNC 网络路径走缓存，本地盘符直读
        self.cache = CacheManager(self.files, cache_root, self._log)
        self.cache.start()
        if self.cache.has_unc:
            self.cache.current_idx = 0

        debug_mode = self.cfg.get("debug_mode", False)

        for i, f in enumerate(self.files):
            if self._stop:
                self.file_done.emit(i, "", "已取消", "warn", "")
                break
            self.file_start.emit(i)
            try:
                # 通知缓存管理器：worker 即将处理任务 i（滑窗推进依据）
                if self.cache.has_unc:
                    self.cache.mark_processing(i)
                # 等待缓存就绪（非 UNC 路径立即返回原始路径）
                local_path = self.cache.wait_until_ready(i)
                if local_path is None:
                    self.file_done.emit(i, "", "缓存失败，已取消", "error", "")
                    break

                # 每个任务独立临时目录
                task_temp_dir = self.cache.task_temp_dir(i)
                os.makedirs(task_temp_dir, exist_ok=True)

                if self.mode == "scan":
                    self._log(f"分析: {f}")
                    tracks, _ = pipeline.analyze_file(
                        local_path, self.cfg,
                        log=lambda m, l="info": self._log(m, l),
                        orig_path=f, temp_dir=task_temp_dir)
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
                    self._log(f"处理: {f}")
                    run_cfg = dict(self.cfg_override)
                    run_cfg["output_overwrite"] = False
                    # v23.8: 清除扫描阶段残留的 TMDB 缓存（处理阶段自动按文件名解析）
                    run_cfg.pop("_tmdb_movie_info", None)
                    if f in self.results:
                        # v22: 跳过标记为 skip 的文件
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
                            local_path, cached, run_cfg,
                            log=lambda m, l="info": self._log(m, l),
                            progress_callback=_on_remux_progress,
                            orig_path=f, temp_dir=task_temp_dir)
                    else:
                        ok, out, msg, tracks = pipeline.process_file(
                            local_path, run_cfg,
                            log=lambda m, l="info": self._log(m, l),
                            orig_path=f, temp_dir=task_temp_dir)
                        self.results[f] = tracks
                    out_path = out or ""
                    plan = fmt_plan(self.results[f])
                    self.file_done.emit(
                        i, plan, "完成" if ok else f"失败: {msg}",
                        "ok" if ok else "error", out_path or "")

                # 完成任务 i：滑窗推进 + 清理窗口外目录（加锁，杜绝竞态误删）
                self.cache.on_task_done(i, debug_mode=debug_mode)

            except Exception as e:
                tb = traceback.format_exc()
                try:
                    sys.stderr.write(
                        f"[Worker异常] 文件 {os.path.basename(f)}:\n{tb}\n")
                except Exception:
                    pass
                self._log(f"[异常] 文件 {os.path.basename(f)}:\n{tb}", "error")
                self.file_done.emit(i, "", f"异常: {e}", "error", "")

            self.progress.emit(i + 1, total)

        # 整个流程结束：清理 config 中残留的 TMDB 缓存
        self.cfg.pop("_tmdb_movie_info", None)
        self.cache.cleanup_all(debug_mode=debug_mode)
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
        v.addWidget(self.table, 3)

        # Controls + progress
        hc = QHBoxLayout()
        self.b_scan = QPushButton("扫描并预览")
        self.b_run = QPushButton("开始处理")
        self.b_save = QPushButton("保存记录")
        self.b_stop = QPushButton("停止当前")
        self.b_stop_all = QPushButton("全部停止")
        self.b_open = QPushButton("打开输出目录")
        self.b_scan.setEnabled(False)
        self.b_run.setEnabled(False)
        self.b_save.setEnabled(False)
        hc.addWidget(self.b_scan)
        hc.addWidget(self.b_run)
        hc.addWidget(self.b_save)
        hc.addWidget(self.b_stop)
        hc.addWidget(self.b_stop_all)
        hc.addStretch(1)
        hc.addWidget(self.b_open)
        hc.addStretch(1)
        hc.addWidget(self.b_open)
        v.addLayout(hc)

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
        self.worker = Worker(self.files, self.results, self.cfg, mode,
                             cfg_override=cfg_override)
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

    def _on_file_start(self, row):
        """文件开始处理 → 整行淡蓝。"""
        self._set_row_color(row, QColor("#1a5276"))

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
        self.b_scan.setEnabled(bool(self.files))
        self.b_run.setEnabled(bool(self.files))
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

    # --------------------------- Other ---------------------------
    def open_settings(self):
        dlg = settings_dialog.SettingsDialog(self.cfg, self)
        if dlg.exec_() == QDialog.Accepted:
            self._reload_config()

    def save_record(self):
        """把当前扫描结果+手动修改保存到 records/ 目录。"""
        if not self.results:
            self.log.log("没有扫描结果可保存。", "warn")
            return
        import json
        records_dir = os.path.join(config_mod.app_root(), "records")
        os.makedirs(records_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        fpath, _ = QFileDialog.getSaveFileName(
            self, "保存扫描记录", os.path.join(records_dir, f"mmf_{timestamp}.json"),
            "扫描记录 (*.json);;所有文件 (*.*)")
        if not fpath:
            return
        data = {
            "version": 1,
            "timestamp": timestamp,
            "files": [],
        }
        for fpath_orig, tracks in self.results.items():
            file_entry = {"path": fpath_orig, "tracks": []}
            for t in tracks:
                if t.track_type in ("audio", "subtitle"):
                    file_entry["tracks"].append({
                        "id": t.track_id,
                        "type": t.track_type,
                        "detected_iso": t.detected_iso,
                        "detected_kind": t.detected_kind,
                        "action": t.action,
                        "name": t.track_name or t.detected_name or "",
                    })
            data["files"].append(file_entry)
        try:
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.log.log(f"扫描记录已保存: {fpath}", "ok")
            self._saved_after_last_mod = True
        except Exception as e:
            self.log.log(f"保存失败: {e}", "error")

    def load_record(self):
        """从 JSON 记录文件导入扫描结果，恢复人工修改。"""
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
        # 重建 files 列表 + results
        from core.probe import Track
        loaded_files = []
        loaded_results = {}
        for fe in data.get("files", []):
            orig = fe["path"]
            loaded_files.append(orig)
            tracks = []
            for te in fe["tracks"]:
                t = Track(stream_index=te["id"], track_id=te["id"],
                          track_type=te["type"], codec="")
                t.detected_iso = te.get("detected_iso")
                t.detected_kind = te.get("detected_kind")
                t.action = te.get("action", "keep")
                t.track_name = te.get("name", "")
                t.detected_name = te.get("name", "")
                tracks.append(t)
            loaded_results[orig] = tracks
        self.files = loaded_files
        self.results = loaded_results
        self._track_data = {}
        self._fill_table()
        # 导入后立即刷新计划动作列
        for i, f in enumerate(self.files):
            if f in self.results:
                from core import policy as pol
                pol.apply_audio_policy(self.results[f], self.cfg)
                pol.apply_subtitle_policy(self.results[f], self.cfg)
                plan = fmt_plan(self.results[f])
                self.table.setItem(i, 4, QTableWidgetItem(plan))
        self.b_scan.setEnabled(True)
        self.b_run.setEnabled(True)
        self.b_save.setEnabled(True)
        self.log.log(f"已导入记录: {fpath} ({len(loaded_files)} 个文件)", "ok")
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
                    if os.path.isdir(fp) and (name.isdigit() or name == "temp"):
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