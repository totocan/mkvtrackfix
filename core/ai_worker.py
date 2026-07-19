# -*- coding: utf-8 -*-
"""
AI 识别隔离子进程 + 管理器（v12 预缓存本地化版）。

改进(v12)：
  - 强制本地化缓存策略：在任务投递前，针对 UNC 网络路径实施“本地缓存”。
    彻底解决 FFmpeg 远程读取导致的 68Mbps 带宽占用与随机读写性能瓶颈。
  - 架构升级：ai_worker 现在承担预检与资源调度，确保交给 ai_child 的全是本地极速 I/O。
"""
import os
import sys
import json
import time
import threading
import queue
import subprocess
import traceback
import shutil
import uuid
import tempfile

_END = object()
_RETRY_MAX = 3          # 连续崩溃最多重试 3 次
_RETRY_INTERVAL = 10    # 每次重试间隔 10 秒
_FULL_COOLDOWN = 60     # 连续崩溃超过上限后冷却 60 秒

class AISubprocessUnavailable(Exception):
    """AI 子进程不可用（无法启动 / 加载失败 / 超时 / 崩溃）。"""

def _plog(msg, stage):
    try:
        from . import logger
        logger.log(msg, stage)
    except Exception:
        sys.__stderr__.write(f"[{stage}] {msg}\n")
    try:
        from . import config as cfg_mod
        log_dir = os.path.abspath(os.path.join(cfg_mod.app_root(), "logs"))
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "mmf_ai_worker.log"), "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [{stage}] {msg}\n")
    except Exception:
        pass

class AIDetector:
    def __init__(self):
        self._proc = None
        self._resp_q = None
        self._req_id = 0
        self._failed_at = None
        self._fail_count = 0  # 连续失败次数
        self._cfg_sig = None
        self._lock = threading.Lock()

    def _ensure_local_copy(self, path):
        """如果文件在网络路径上，将其复制到本地临时目录。"""
        # 判断 UNC 路径 (// 开头) 或映射的网络驱动器
        if path.startswith(("\\\\", "//")) or (len(path) > 2 and path[1] == ':' and not os.path.exists(path[:2])):
            _plog(f"检测到网络/UNC路径，正在进行本地缓存预载: {path}", "AI_DISK")
            temp_path = os.path.join(tempfile.gettempdir(), f"ai_cache_{uuid.uuid4().hex}_{os.path.basename(path)}")
            try:
                shutil.copy2(path, temp_path)
                _plog(f"本地缓存就绪: {temp_path}", "AI_DISK")
                return temp_path, True # 返回路径和是否需要后续清理
            except Exception as e:
                _plog(f"本地缓存预载失败: {e}", "AI_DISK")
                return path, False
        return path, False

    def _is_in_cooldown(self):
        """判断是否处于完全冷却期（连续崩溃超过上限后短暂冻结）。"""
        if self._failed_at is None:
            return False
        elapsed = time.time() - self._failed_at
        # 连续失败未超上限 → 不冷却，允许立即重试
        if self._fail_count <= _RETRY_MAX:
            if elapsed >= _RETRY_INTERVAL:
                self._failed_at = None
            return False  # 允许重试
        # 连续失败超上限 → 完全冷却 _FULL_COOLDOWN 秒
        if elapsed >= _FULL_COOLDOWN:
            self._failed_at = None
            self._fail_count = 0
            return False
        return True

    def _mark_failed(self):
        self._failed_at = time.time()
        self._fail_count += 1

    @staticmethod
    def _sig(cfg):
        return (cfg.get("model_size"), cfg.get("device"), cfg.get("compute_type"), int(cfg.get("cpu_threads", 0)))

    def _spawn(self, cfg):
        from . import config as cfg_mod
        app_root = os.path.abspath(cfg_mod.app_root())
        py = os.path.abspath(sys.executable)
        
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        boot_script = f"import sys, runpy; sys.path.insert(0, {repr(app_root)}); runpy.run_module('core.ai_child', run_name='__main__')"
        cmd = [py, "-c", boot_script]

        try:
            self._proc = subprocess.Popen(
                cmd, cwd=app_root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, text=True, bufsize=1, errors="replace", startupinfo=startupinfo
            )
        except Exception as e:
            _plog(f"子进程启动失败: {e}", "AI_CRASH")
            self._mark_failed()
            return False

        self._resp_q = queue.Queue()
        self._start_reader()
        
        try:
            self._proc.stdin.write(json.dumps(cfg, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
        except Exception:
            self._clear()
            return False

        resp = self._read_resp(timeout=900)
        return resp is not None and resp.get("t") != "load_error"

    def _start_reader(self):
        def _read_out():
            for line in iter(self._proc.stdout.readline, ""):
                line = line.strip()
                if not line: continue
                try: obj = json.loads(line)
                except Exception: continue
                self._resp_q.put(obj)
            self._resp_q.put(_END)
        threading.Thread(target=_read_out, daemon=True).start()

    def _read_resp(self, timeout):
        try: return self._resp_q.get(timeout=timeout)
        except queue.Empty: return None

    def _clear(self):
        if self._proc:
            try: self._proc.terminate()
            except: pass
            self._proc = None

    def detect(self, wav_path, title_hint=None, config=None):
        cfg = dict(config or {})

        # v22: 自动重试机制 — 崩溃后尝试重启子进程，而非干等冷却期
        if self._is_in_cooldown():
            _plog(f"AI 子进程冷却中（{self._fail_count}次失败），等待 {_RETRY_INTERVAL}s 后重试", "AI")
            time.sleep(_RETRY_INTERVAL)
            self._failed_at = None
            # 冷却后再尝试一次，如果还不行就放弃
            if self._is_in_cooldown():
                raise AISubprocessUnavailable("冷却期")

        # 【核心逻辑】：本地化预缓存

        # 【核心逻辑】：本地化预缓存
        working_path, needs_cleanup = self._ensure_local_copy(wav_path)
        
        try:
            sig = self._sig(cfg)
            if (self._proc is None or self._proc.poll() is not None or sig != self._cfg_sig):
                self._cfg_sig = sig
                if not self._spawn(cfg):
                    raise AISubprocessUnavailable("启动失败")

            with self._lock:
                self._req_id += 1
                self._proc.stdin.write(json.dumps({"id": self._req_id, "wav": working_path, "title": title_hint}) + "\n")
                self._proc.stdin.flush()

            obj = self._read_resp(timeout=int(cfg.get("ai_timeout", 300)))
            if obj is None or obj is _END:
                self._clear()
                self._mark_failed()
                raise AISubprocessUnavailable("子进程崩溃")
            
            if obj.get("t") == "ok": return obj.get("out")
            if obj.get("t") == "err": raise RuntimeError(obj.get("e"))
            
        finally:
            # 【清理】：调试模式保留供排查
            if needs_cleanup and os.path.exists(working_path) \
                    and not cfg.get("debug_mode", False):
                try: os.remove(working_path)
                except: pass

    def shutdown(self):
        self._clear()