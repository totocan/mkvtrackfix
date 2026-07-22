# -*- coding: utf-8 -*-
"""Entry point: add project root to sys.path, set up file logging,
and launch GUI. Any unhandled exception is written to a log file
(and shown in a message box on Windows) so the user can share it."""
import os
import sys
import traceback
import datetime

# 集中式阶段化日志器（在所有模块之前可用；sink 稍后由本文件注入真实文件）
try:
    from core import logger
except Exception:  # 极端情况下 core 不可导入时退化为 no-op
    class _NullLogger:
        @staticmethod
        def set_sink(*a, **k):
            pass
        @staticmethod
        def log(*a, **k):
            pass
        @staticmethod
        def disable(*a, **k):
            pass
    logger = _NullLogger()

# ---------------------------------------------------------------------------
# Project root (works both source-run and frozen exe)
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, "frozen", False):
    _APP_ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:
    _APP_ROOT = _HERE
sys.path.insert(0, _HERE)

# 将 CWD 固定到项目根目录（绿色便携包：所有工具路径均为相对路径，
# 依赖子进程正确的工作目录来查找 tools\ffmpeg.exe 等）。
os.chdir(_APP_ROOT)
import tempfile

# 所有临时文件放到项目 tmp/temp/ 下（统一管理，方便清理）
_TEMP_DIR = os.path.join(_APP_ROOT, "tmp", "temp")
os.makedirs(_TEMP_DIR, exist_ok=True)
tempfile.tempdir = _TEMP_DIR

# ---------------------------------------------------------------------------
# File logging (always on; written to logs/ directory)
# ---------------------------------------------------------------------------
_LOG_DIR = os.path.join(_APP_ROOT, "logs")
os.makedirs(_LOG_DIR, exist_ok=True)

_LOG_FILE = os.path.join(
    _LOG_DIR,
    f"mmf_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
)


class _FileLogHandler:
    """Simple handler that writes to both stderr and the log file."""

    def __init__(self, path):
        self._path = path
        self._dir = os.path.dirname(path)
        self._fh = open(path, "a", encoding="utf-8", buffering=1)
        self._fh.write(f"\n{'='*60}\n")
        self._fh.write(f"MediaMetaFixer started at {datetime.datetime.now()}\n")
        self._fh.write(f"Python: {sys.executable}\n")
        self._fh.write(f"Version: {sys.version}\n")
        self._fh.write(f"Frozen: {getattr(sys,'frozen',False)}\n")
        self._fh.write(f"AppRoot: {_APP_ROOT}\n")
        self._fh.write(f"LogFile: {self._path}\n")
        self._fh.write(f"{'='*60}\n\n")

    def write(self, text):
        sys.__stderr__.write(text)
        try:
            self._fh.write(text)
            self._fh.flush()
        except Exception:
            pass

    def flush(self):
        sys.__stderr__.flush()
        try:
            self._fh.flush()
        except Exception:
            pass


_FILE_LOG = _FileLogHandler(_LOG_FILE)
sys.stderr = _FILE_LOG

# 把真实日志文件对象注入集中式日志器，使所有模块的 logger.log() 都落盘到
# 同一个 logs/mmf_*.log（faulthandler 也写这个文件的真实 fd，互不冲突）。
try:
    logger.set_sink(_FILE_LOG._fh)
except Exception:
    pass
logger.log("MediaMetaFixer 启动", "SYSTEM")
logger.log(f"Python: {sys.executable}", "SYSTEM")
logger.log(f"Version: {sys.version.split()[0]}", "SYSTEM")
logger.log(f"Frozen: {getattr(sys, 'frozen', False)}", "SYSTEM")
logger.log(f"AppRoot: {_APP_ROOT}", "SYSTEM")
logger.log(f"LogFile: {_LOG_FILE}", "SYSTEM")

# ---------------------------------------------------------------------------
# faulthandler: dump a Python traceback to the log file on NATIVE crashes
# (segfault / access violation inside C extensions like ctranslate2, onnxruntime,
# or a killed subprocess). Python try/except and sys.excepthook cannot catch
# these, so without this the process just dies silently.
# ---------------------------------------------------------------------------
try:
    import faulthandler
    # IMPORTANT: faulthandler needs a REAL file object (with fileno) to
    # write the crash dump on Windows. Our _FileLogHandler wrapper has no
    # valid fd, so pass its underlying _fh instead.
    _crash_file = getattr(_FILE_LOG, "_fh", sys.stderr)
    faulthandler.enable(file=_crash_file, all_threads=True)
    logger.log("faulthandler 已启用（原生崩溃将写入此日志文件）", "SYSTEM")
except Exception as e:
    sys.__stderr__.write(f"faulthandler enable failed: {e}\n")


def get_log_path():
    """Return current log file path (for UI display)."""
    return _LOG_FILE


def get_log_dir():
    """Return logs directory."""
    return _LOG_DIR


def show_error(msg):
    """Windows native error dialog + write to log."""
    full = f"[{datetime.datetime.now().isoformat()}] {msg}"
    sys.stderr.write(full + "\n")
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, str(msg), "MediaMetaFixer Error", 0x10)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Global exception hook: catch EVERY crash and write to log file
# ---------------------------------------------------------------------------
_original_excepthook = sys.excepthook


def _global_excepthook(exc_type, exc_value, exc_tb):
    tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    msg = (
        f"\n{'!'*60}\n"
        f"UNHANDLED EXCEPTION ({datetime.datetime.now()}):\n"
        f"{tb_text}\n"
        f"{'!'*60}\n"
        f"Log file: {_LOG_FILE}\n"
    )
    sys.stderr.write(msg)
    show_error(
        "程序发生未捕获的错误，已写入日志文件：\n\n"
        f"{_LOG_FILE}\n\n"
        "请将此日志文件贴给开发者以协助排查。\n\n"
        f"错误摘要：{exc_type.__name__}: {exc_value}"
    )
    _original_excepthook(exc_type, exc_value, exc_tb)


sys.excepthook = _global_excepthook

# 退出时清理 AI 识别子进程（避免孤儿进程）
import atexit


def _shutdown_ai():
    try:
        from core import audio_detect
        audio_detect.shutdown()
    except Exception:
        pass


atexit.register(_shutdown_ai)

# Also hook thread exceptions (Worker threads often swallow errors)
try:
    import threading
    _orig_thread_excepthook = threading.excepthook

    def _thread_excepthook(args):
        msg = (
            f"\n{'!'*60}\n"
            f"THREAD EXCEPTION ({datetime.datetime.now()}) "
            f"thread={args.thread.name}:\n"
            f"{''.join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))}\n"
            f"{'!'*60}\n"
        )
        sys.stderr.write(msg)
        if _orig_thread_excepthook:
            _orig_thread_excepthook(args)

    threading.excepthook = _thread_excepthook
except Exception:
    pass  # older Python may not have this

# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    sys.stderr.write("Starting MediaMetaFixer GUI...\n")
    try:
        from gui.main_window import main as _gui_main
        _gui_main()
    except SystemExit:
        raise
    except Exception:
        err = traceback.format_exc()
        sys.stderr.write(f"Fatal startup error:\n{err}\n")
        show_error(
            "启动失败，详情见下方（也可在命令行运行查看）：\n\n"
            f"{err}\n\n"
            f"日志文件：{_LOG_FILE}"
        )
        raise
