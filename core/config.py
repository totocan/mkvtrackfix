# -*- coding: utf-8 -*-
"""
配置：默认值 + JSON 持久化 + 工具路径自动探测。
配置文件位于脚本同目录 config.json。

改进(v21.2)：
  - 音轨采样默认时长从100秒改为10秒（faster-whisper 足够）
  - 音轨三段采样改为批量提取再统一AI识别
  - 图像字幕抽帧后新增采样算法（60帧hash去重→~30帧）提升OCR效率
  - Tesseract → PaddleOCR（深度学习OCR引擎，简繁识别更准确）
  - 日志系统修复：logger.log() 现在也写入文件日志（sys.stderr）
  - 完成一个任务后清空 tmp/temp/ 目录
改进(v9+)：
  - 字幕 OCR 采用 Tesseract（不再依赖 Subtitle Edit）
  - 新增 GUI 字体大小/字体族设置（解决界面文字偏小问题）
  - 工具路径每次启动都验证，旧路径不存在则清除并重新探测
  - 去掉硬编码 Windows 绝对路径，只保留：项目根 tools/ → PATH → shutil.which
"""
import json
import os
import sys
import shutil


# 应用版本号：每次迭代 +1（同步修改此处和 README 即可）
APP_VERSION = "v23.27"


DEFAULTS = {
    # —— 配置版本（v21.2：版本不一致时自动重置为默认值）——
    "_schema_version": APP_VERSION,

    # —— AI 模型 ——
    "model_size": "medium",      # tiny/base/small/medium/large-v3
    "device": "cpu",             # cpu / cuda
    "compute_type": "int8",      # int8 / int16 / float16 / float32
    "cpu_threads": 0,            # 0=自动
    "sample_start_seconds": 300,    # 已废弃，v21.2 起改用 sample_segments
    "sample_duration_seconds": 10,   # 音轨识别每段采样时长秒数（v21.2：10秒）
    "sample_segments": "600,1000,1500",  # 多段采样起点（逗号分隔，AI 识别用）
    "ai_timeout": 300,           # 单文件 AI 识别等待子进程响应的超时(秒)
    "zh_audio_as": "cmn",        # zh 默认映射 cmn / yue
    "audio_redetect": "all",     # all / und_only / skip

    # —— 工具路径（留空则自动探测）——
    "mkvmerge_path": "",
    "ffmpeg_path": "",
    "ffprobe_path": "",

    # —— RapidOCR 配置（v22：替代 PaddleOCR，基于 ONNX Runtime，快速轻量）——
    "ocr_skip_seconds": 300,      # OCR 抽帧跳过前 N 秒（避开片头特效密集区）
    "ocr_min_text_len": 30,       # OCR 合并文本最小长度，低于此值视为失败走启发式推断
    "ocr_max_attempts": 4,        # OCR 最多尝试次数（默认 4）

    # —— mkvextract 字幕抽取超时（秒），13GB 大文件可能超过 45 秒 ——
    "subtitle_extract_timeout": 180,

    # —— GUI 字体 ——
    "gui_font_family": "",        # 空=系统默认（Windows 通常为 Microsoft YaHei UI）
    "gui_font_size": 10,          # 界面字体大小（pt），菜单栏/表格/按钮统一使用
    "log_font_size": 9,           # 日志区域等宽字体大小（pt），略小于主界面

    # —— 音轨策略 ——
    "audio_reduce": True,
    "audio_keep_best_only": True,

    # —— 字幕策略 ——
    "sub_remove_traditional": True,
    "sub_remove_pure_english_if_bilingual": True,  # 有简中/双语时去掉纯英文
    "sub_remove_redundant_simplified_if_bilingual": True,  # 有简中英双语时移除冗余的独立简体中文

    # —— 电影产地判断 ——
    "douban_enabled": True,      # 默认开启豆瓣查询，可选关闭
    "domestic_drop_english": True,  # 国产电影去掉英语音轨

    # —— 输出 ——
    "smart_rename": True,        # v22: 智能重命名（默认开启）
    "output_suffix": ".fixed",   # 智能重命名关闭时使用的后缀

    # —— 日志 ——
    "verbose_tools": True,

    # —— 调试 ——
    "debug_mode": False,
    "keep_ocr_frames": False,    # v23.21: 保留OCR帧/WAV文件供排查（不清tmp/N/temp/）

    # —— 源 ——
    "recursive": True,
    "extensions": ["mp4", "mkv"],
    "source_path": "",
}

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "config.json")


def _base_dir():
    """项目根目录：源码运行取脚本所在目录；打包后取 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def app_root():
    """项目根目录（放 tools/ models/ config.json 的地方）。

    - 源码运行：config.py 在 core/，根目录为其父目录。
    - 打包(frozen)：取 exe 所在目录。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_config_path():
    """确定配置文件路径：
      - 优先放在 exe/脚本同目录（便于便携）；
      - 若该目录不可写（如装进 Program Files），回退到 %APPDATA%。
    """
    p = os.path.join(_base_dir(), "config.json")
    if os.path.exists(p):
        return p
    try:
        if os.access(os.path.dirname(p) or ".", os.W_OK):
            return p
    except OSError:
        pass
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(appdata, "MediaMetaFixer")
    try:
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "config.json")
    except OSError:
        return p


# 需要被"相对化"处理的工具路径键（相对项目根目录 tools/）
_TOOL_PATH_KEYS = ("mkvmerge_path", "ffmpeg_path", "ffprobe_path")


def normalize_tool_paths(cfg):
    """把工具路径相对化 / 清理失效的绝对路径。

    - 若探测到的路径位于程序目录（app_root）内，存为**相对路径**
      （如 tools\\mkvmerge.exe），这样迁移 / 重装后不会因绝对路径失效。
    - 若用户手动指定了程序目录外的绝对路径，保留原样（兼容自定义安装）。
    - 若路径已失效（文件不存在），清空为""，下次启动自动重新探测。
    返回新的 cfg 副本（不修改入参）。
    """
    root = app_root()
    root_norm = os.path.normcase(os.path.normpath(root))
    out = dict(cfg)
    for k in _TOOL_PATH_KEYS:
        v = cfg.get(k) or ""
        if not v:
            out[k] = ""
            continue
        if not os.path.exists(v):
            # 失效路径 → 清空，下次自动探测
            out[k] = ""
            continue
        try:
            abs_v = os.path.normcase(os.path.normpath(os.path.abspath(v)))
        except Exception:
            continue
        # 若位于程序根目录内，转成相对路径
        if abs_v.startswith(root_norm + os.sep) or abs_v == root_norm:
            rel = os.path.relpath(abs_v, root_norm)
            out[k] = rel.replace("/", "\\")  # 统一为 Windows 分隔符风格
        # 否则保留绝对路径（用户自定义外部安装）
    return out


def load(path=None):
    path = path or resolve_config_path()
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # v21.2: 检查 schema_version，版本不匹配时重置配置
            stored_version = data.get("_schema_version", "")
            if stored_version != APP_VERSION:
                # 局部 import 避免循环导入
                try:
                    from . import logger as _lg
                    _lg.log(f"配置版本从 {stored_version or '(无)'} 升级到 {APP_VERSION}，"
                            f"恢复默认配置（保留工具路径）", "SYSTEM")
                except Exception:
                    pass
                # 只保留探测到的工具路径
                for k in _TOOL_PATH_KEYS:
                    if k in data and data[k]:
                        cfg[k] = data[k]
            else:
                cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
        except Exception:
            pass
    # 每次启动都验证工具路径并重新探测（避免旧版残留绝对路径）
    cfg = autodetect_tools(cfg)
    # 把探测到的路径相对化（相对程序根），便于迁移
    cfg = normalize_tool_paths(cfg)
    return cfg


def save(cfg, path=None):
    path = path or resolve_config_path()
    # 保存前也做相对化处理，避免写入绝对路径
    cfg = normalize_tool_paths(cfg)
    data = {k: cfg.get(k, v) for k, v in DEFAULTS.items()}
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _validate_path(p):
    """验证路径是否存在。空字符串视为未指定（需探测），非空但不存在则视为失效。"""
    if not p:
        return False  # 未指定，需要探测
    return os.path.exists(p)


def autodetect_tools(cfg):
    """验证并探测工具路径。

    改进(v9)：
    - 每次启动都验证已有路径是否存在，不存在则清除并重新探测
    - 新增 Tesseract 探测（tools/tesseract/tesseract.exe → PATH）
    - 去掉硬编码 Windows 绝对路径，只保留：项目根 tools/ → PATH
    - 避免版本迭代后旧版绝对路径指向错误位置
    """
    root = app_root()

    def _check(cands):
        for c in cands:
            if c and os.path.exists(c):
                return c
        return None

    # mkvmerge：验证已有路径，失效则重新探测
    if not _validate_path(cfg.get("mkvmerge_path")):
        cfg["mkvmerge_path"] = _check([
            os.path.join(root, "tools", "mkvmerge.exe"),
            os.path.join(root, "tools", "MKVToolNix", "mkvmerge.exe"),
            shutil.which("mkvmerge"),
        ])

    # ffmpeg / ffprobe（绿色包放在 tools/）
    if not _validate_path(cfg.get("ffmpeg_path")):
        cfg["ffmpeg_path"] = _check([
            os.path.join(root, "tools", "ffmpeg.exe"),
            os.path.join(root, "tools", "bin", "ffmpeg.exe"),
            shutil.which("ffmpeg"),
        ]) or ""
    if not _validate_path(cfg.get("ffprobe_path")):
        cfg["ffprobe_path"] = _check([
            os.path.join(root, "tools", "ffprobe.exe"),
            os.path.join(root, "tools", "bin", "ffprobe.exe"),
            shutil.which("ffprobe"),
        ]) or ""

    # —— PaddleOCR 无需探测路径——pip 安装后直接 import 即可

    return cfg


def _resolve_tool(cfg, key):
    """解析工具路径：相对路径（相对程序根）转绝对，绝对路径/空值原样返回。

    配合 normalize_tool_paths 使用——配置里存的是相对路径（如
    tools\\mkvmerge.exe），运行时拼回程序根得到真实绝对路径。
    """
    p = (cfg.get(key) or "").strip()
    if not p:
        return ""
    if os.path.isabs(p):
        return p
    return os.path.normpath(os.path.join(app_root(), p))


def resolve_mkvmerge(cfg):
    p = _resolve_tool(cfg, "mkvmerge_path")
    return p or "mkvmerge"
