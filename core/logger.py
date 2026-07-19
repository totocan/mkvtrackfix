# -*- coding: utf-8 -*-
"""
集中式、带阶段标签的结构化日志器（MediaMetaFixer）。

设计目标（回应"日志是否该是多个环节的集合"这一疑问）：

    整个处理流程 = 多阶段（SYSTEM / PIPELINE / AI / TOOLS / GUI）的集合。
    我们不把日志拆成多个文件让用户自己拼——而是统一收口到**一个**
    logs/mmf_<时间戳>.log，每条记录带 [阶段] 标签，便于按标签过滤定位：

        [21:00:01][SYSTEM ] MediaMetaFixer 启动
        [21:00:02][PIPELINE] == STAGE probe == 解析轨道: D:/x/movie.mkv
        [21:00:03][AI     ] 加载模型 medium (device=cpu, compute_type=int8)...
        [21:00:05][AI     ] 模型就绪 (1.2s)
        [21:00:06][TOOLS  ] ffmpeg 抽取音轨 -> .../tmp.wav
        [21:00:06][TOOLS  ] ffmpeg: ok
        [21:00:09][AI     ] 识别结果: zh prob=0.97 -> 普通话(cmn)
        [21:00:10][TOOLS  ] mkvmerge -J ... ok
        [21:00:12][TOOLS  ] mkvmerge: ok
        [21:00:12][PIPELINE] == STAGE policy == 应用保留策略
        ...

第三方工具（ffmpeg / mkvmerge / Tesseract）本身不写独立日志文件，
它们的诊断信息都走 stdout/stderr——由 core/utils.run() 捕获后带 [TOOLS]
标签写进来（见 utils.run 的 log_stage 参数）。

无需任何参数即可启用（始终开启）；sink 由 main.py 在打开真实日志文件后注入。
"""
import sys
import threading
import datetime

# 文件对象（真实 fd，由 main.py 注入）。为 None 时回退到 sys.__stderr__。
_sink = None
# 总开关。日志始终开启，无需参数；极端情况下可 disable()。
_ENABLED = True
_lock = threading.Lock()

# 阶段标签宽度对齐（中文/英文统一按 8 宽补空格）
_STAGE_NAMES = {
    "SYSTEM": "SYSTEM",
    "PIPELINE": "PIPELINE",
    "AI": "AI",
    "TOOLS": "TOOLS",
    "GUI": "GUI",
}


def set_sink(fh):
    """注入真实日志文件对象（main.py 在打开 logs/mmf_*.log 后调用）。

    fh 必须是一个带 .write()/.flush() 的文件对象（推荐直接传 _FILE_LOG._fh，
    即真实 fd，这样 faulthandler 与本日志器写同一个文件且不冲突）。
    """
    global _sink
    _sink = fh


def disable():
    global _ENABLED
    _ENABLED = False


def enable():
    global _ENABLED
    _ENABLED = True


def log(msg, stage="SYSTEM"):
    """写一条带阶段标签的日志。线程安全。

    msg: 任意字符串（可多行）。
    stage: SYSTEM / PIPELINE / AI / TOOLS / GUI（其它值也接受，会被对齐）。
    """
    if not _ENABLED:
        return
    tag = _STAGE_NAMES.get(stage, stage)
    if len(tag) < 8:
        tag = tag.ljust(8)
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    # 多行消息：首行带标签，后续行缩进对齐，便于阅读
    lines = str(msg).split("\n")
    out = []
    for i, ln in enumerate(lines):
        if i == 0:
            out.append(f"[{ts}][{tag}] {ln}\n")
        else:
            out.append(f"{'':>19} {ln}\n")
    blob = "".join(out)
    with _lock:
        try:
            if _sink is not None:
                _sink.write(blob)
                _sink.flush()
        except Exception:
            pass
        # 写入重定向后的 sys.stderr（main.py 已将其指向日志文件 _FILE_LOG），
        # 确保所有模块通过 logger.log() 发出的日志都落盘到 logs/mmf_*.log。
        try:
            sys.stderr.write(blob)
            sys.stderr.flush()
        except Exception:
            pass
        # 控制台也输出一份（从命令行运行时有用）
        try:
            sys.__stderr__.write(blob)
            sys.__stderr__.flush()
        except Exception:
            pass


def tee_stage(stage):
    """装饰器工厂：把函数内 print 的内容也带标签写进日志。预留扩展。"""
    def _decor(fn):
        def _wrap(*a, **kw):
            try:
                return fn(*a, **kw)
            except Exception:
                raise
        return _wrap
    return _decor
