# -*- coding: utf-8 -*-
"""
通用工具：子进程执行、ffprobe 解析、ffmpeg 抽取轨道、mkvmerge 识别、Tesseract OCR。
跨平台（Windows / Linux），支持 UNC 网络路径。

改进(v9)：
  - 新增 extract_frames_with_subtitle：ffmpeg 抽帧并烧录字幕（用于图像字幕 OCR）
  - 新增 ocr_image_with_tesseract：调用 Tesseract CLI 识别单张/多张图片文字
  - 新增 _find_tesseract / set_tesseract_path：Tesseract 路径解析与外部注入
"""
import json
import re
import os
import subprocess
import sys
import tempfile

from . import logger


# ---------------------------------------------------------------------------
# ffmpeg / ffprobe 路径解析（支持绿色包：tools/ 相对目录优先于 PATH）
# ---------------------------------------------------------------------------
_FFMPEG = None
_FFPROBE = None
_VERBOSE_TOOLS = False
_ERR_CAP = 4000     # 失败时 stderr 截取上限
_VERB_CAP = 32000   # verbose 模式输出上限
_RAPID_OCR = None


def set_ffmpeg_paths(ffmpeg=None, ffprobe=None):
    """设置 ffmpeg/ffprobe 路径（由 main_window 启动时注入配置值）。"""
    global _FFMPEG, _FFPROBE
    _FFMPEG = ffmpeg or None
    _FFPROBE = ffprobe or None


def set_verbose_tools(on):
    """是否详细记录第三方工具输出（verbose_tools 配置项注入）。"""
    global _VERBOSE_TOOLS
    _VERBOSE_TOOLS = bool(on)


def _find_bin(name):
    """返回 ffmpeg/ffprobe 的完整路径（由 set_ffmpeg_paths 注入或回退 PATH）。"""
    if name == "ffmpeg" and _FFMPEG:
        return _FFMPEG
    if name == "ffprobe" and _FFPROBE:
        return _FFPROBE
    return name  # fallback: rely on PATH


def _init_rapid_ocr():
    """全局初始化 RapidOCR-onnxruntime 实例（v22 替代 PaddleOCR）。

    - 基于 ONNX Runtime（和 faster-whisper 共用，无额外框架）
    - 无参初始化：RapidOCR()
    - 调用：ocr(img_path) → ([box, (text, score)], elapse)
    """
    global _RAPID_OCR
    if _RAPID_OCR is not None:
        return True
    try:
        from rapidocr_openvino import RapidOCR
        _RAPID_OCR = RapidOCR()
        return True
    except Exception as e:
        from . import logger as _lg
        _lg.log(f"RapidOCR 初始化失败: {e}（请运行 build_portable.bat 安装依赖）", "SYSTEM")
        return False


def ocr_image_with_rapid(image_paths, config=None):
    """用 RapidOCR 对一批图片做文字识别，返回 (文本, 是否成功)。

    RapidOCR 默认模型内置简体中文+英文混合识别。
    简繁判断由调用方（classify_subtitle_text）基于文本自身判断。
    结果格式：[[x1,y1,x2,y2,x3,y3,x4,y4], (text, score)]
    速度远快于 PaddleOCR，CPU 推理轻量高效。
    """
    cfg = config or {}
    min_len = int(cfg.get("ocr_min_text_len", 30))
    if not _init_rapid_ocr():
        logger.log("RapidOCR 不可用，跳过 OCR", "TOOLS")
        return "", False
    parts = []
    for img_path in image_paths:
        if not os.path.exists(img_path):
            continue
        try:
            result, elapse = _RAPID_OCR(img_path)
            if result and result != (None, None):
                for line in result:
                    if line and len(line) > 1 and isinstance(line[1], str):
                        text = line[1].strip()
                        if text:
                            parts.append(text)
        except Exception as e:
            logger.log(f"RapidOCR 识别 {os.path.basename(img_path)} 异常: {e}", "TOOLS")
    combined = "\n".join(parts).strip()
    preview = combined[:120].replace("\n", "↵") if combined else "(空)"
    logger.log(f"RapidOCR 识别({len(combined)}B): {preview}", "TOOLS")
    if not combined or len(combined) < min_len:
        return "", False
    return combined, True


def _init_paddle_ocr(config=None):
    """全局初始化 PaddleOCR v3.7 实例（延迟加载，只初始化一次）。

    兼容 PaddlePaddle 3.2.0 + PaddleOCR 3.7.x（锁定版本）。
    初始化：PaddleOCR(lang='ch')
    调用：ocr.predict(img_path) → 返回 dict 含 rec_texts 列表
    """
    global _PADDLE_OCR
    if _PADDLE_OCR is not None:
        return True
    try:
        from paddleocr import PaddleOCR
        # v3.7 构造函数仅传 lang，其余用默认值
        _PADDLE_OCR = PaddleOCR(lang='ch')
        return True
    except Exception as e:
        from . import logger as _lg
        _lg.log(f"PaddleOCR 初始化失败: {e}（请先运行 build_portable.bat 安装依赖）", "SYSTEM")
        return False


def _paddle_ocr_batch(ocr_instance, img_paths):
    """对一批图片做 PaddleOCR 识别，返回合并文本列表。

    PaddleOCR v3.7 predict() 返回迭代器，结果格式：
      [{'input_path':..., 'page_index':..., 'rec_texts':[...], 'rec_scores':[...]}, ...]
    """
    parts = []
    for img_path in img_paths:
        if not os.path.exists(img_path):
            continue
        try:
            results = list(ocr_instance.predict(img_path))
            if results and len(results) > 0:
                r = results[0]
                texts = r.get("rec_texts", [])
                parts.extend(t for t in texts if t and t.strip())
        except Exception as e:
            from . import logger as _lg
            _lg.log(f"PaddleOCR 识别 {os.path.basename(img_path)} 异常: {e}", "TOOLS")
    return parts


def app_root():
    """返回项目根目录绝对路径，供子进程设置 cwd 使用（绿色便携包路径相对化）。"""
    from . import config as cfg_mod
    return cfg_mod.app_root()


class CmdError(Exception):
    """命令执行失败。"""

    def __init__(self, cmd, rc, stderr):
        self.cmd = cmd
        self.rc = rc
        self.stderr = stderr
        super().__init__(f"命令失败 (rc={rc}): {' '.join(cmd)}\n{stderr}")


def run(cmd, timeout=None, quiet=False, log_stage=None, label=None):
    """执行命令（列表形式），返回 (returncode, stdout, stderr)。

    log_stage: 若给定（通常为 "TOOLS"），则把本次调用的命令与第三方程序的
                stdout/stderr 带标签写进结构化日志：
                  - 调用时记录完整命令行；
                  - 失败时记录 stderr 尾部（封顶 _ERR_CAP）；
                  - 成功且 _VERBOSE_TOOLS 开启时记录完整输出（封顶 _VERB_CAP）；
                  - 成功且未开启 verbose 时仅记录 "ok"。
    label: 日志里展示的工具名（默认取可执行文件名）。
    """
    if isinstance(cmd, str):
        cmd = cmd.split()
    disp = label or (os.path.basename(cmd[0]) if cmd else "cmd")
    if log_stage is not None:
        logger.log(f"{disp}: {' '.join(cmd)}", log_stage)
    try:
        # 绿色便携包：所有工具路径均为相对路径，cwd 固定为项目根目录
        from . import config as cfg_mod
        # v21.2: 明确指定 UTF-8 编码（mkvmerge/ffmpeg/Tesseract 均输出 UTF-8），
        # 避免中文 Windows 默认 GBK 解码导致路径乱码（如 mkvmerge -J 的 file_name 字段）
        proc = subprocess.run(
            cmd,
            cwd=cfg_mod.app_root(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as e:
        if log_stage is not None:
            logger.log(f"{disp}: 超时({timeout}s)，进程被杀死", log_stage)
        raise CmdError(cmd if isinstance(cmd, list) else [str(cmd)],
                       -1, f"超时({timeout}s)") from e
    except FileNotFoundError as e:
        # 可执行文件不存在（如 ffprobe 未安装、不在 PATH、不在绿色包 tools/）。
        # 转成清晰可读的 CmdError，而不是裸的 WinError 2 堆栈。
        tool = cmd[0] if isinstance(cmd, list) and cmd else str(cmd)
        msg = (f"命令未找到: {tool}（未安装 / 不在 PATH / "
               f"不在绿色包 tools/ 中）")
        if log_stage is not None:
            logger.log(f"{disp}: {msg}", log_stage)
        raise CmdError(cmd if isinstance(cmd, list) else [str(cmd)],
                       -2, msg) from e

    if log_stage is not None:
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-_ERR_CAP:]
            logger.log(f"{disp}: 失败 rc={proc.returncode}\n{tail}", log_stage)
        elif _VERBOSE_TOOLS:
            full = (proc.stderr or proc.stdout or "")[:_VERB_CAP]
            if full.strip():
                logger.log(f"{disp}: ok\n{full}", log_stage)
            else:
                logger.log(f"{disp}: ok", log_stage)
        else:
            logger.log(f"{disp}: ok", log_stage)
    return proc.returncode, proc.stdout, proc.stderr


def ffprobe_json(path):
    """返回 ffprobe 的 JSON 解析结果（含 streams / format）。
    
    网络极限优化版：
      - 限制仅读取 500KB 头部数据 (-probesize 500K)。
      - 限制流分析时长在 500 毫秒内 (-analyzeduration 500ms)，防止大文件网络二次扫描。
    """
    rc, out, err = run([
        _find_bin("ffprobe"), 
        "-v", "quiet", 
        "-probesize", "500K",                  # 限制只读 500KB 头部数据量
        "-analyzeduration", "500ms",            # 限制分析时间 500 毫秒
        "-print_format", "json",
        "-show_streams", 
        "-show_format", 
        "-i", path,                            # 限制参数必须放在输入文件 -i 之前生效
    ], timeout=60, log_stage="TOOLS", label="ffprobe")
    if rc != 0:
        raise CmdError(["ffprobe", path], rc, err)
    return json.loads(out)


def mkvmerge_identify(path):
    """
    返回 mkvmerge 识别信息（使用 -J JSON，含语言）：
      tracks = [{'id': int, 'type': 'video'|'audio'|'subtitle',
                 'codec': str, 'language': str|None}, ...]  按文件顺序排列
    """
    from . import config as cfg_mod
    cfg = cfg_mod.load()
    exe = cfg_mod.resolve_mkvmerge(cfg) or "mkvmerge"
    rc, out, err = run([exe, "-J", path], timeout=120,
                       log_stage="TOOLS", label="mkvmerge-识别")
    if rc != 0:
        raise CmdError([exe, "-J", path], rc, err)
    data = json.loads(out)
    tracks = []
    for t in data.get("tracks", []):
        rtype = t.get("type", "")
        # mkvmerge -J 中字幕类型为 "subtitles"，归一化
        if rtype in ("subtitles", "subtitle"):
            rtype = "subtitle"
        if rtype not in ("video", "audio", "subtitle"):
            continue
        props = t.get("properties", {})
        entry = {
            "id": t.get("id"),
            "type": rtype,
            "codec": t.get("codec", ""),
            "language": props.get("language"),
            "language_ietf": props.get("language_ietf"),
            # 回退路径（无 ffprobe 时）需要这些字段
            "channels": props.get("audio_channels"),
            "title": props.get("track_name") or t.get("track_name"),
        }
        # v22: 视频属性（供智能重命名使用）
        if rtype == "video":
            # v22: 多种字段名兜底（mkvmerge v100 实际是 pixel_dimensions / display_dimensions 字符串）
            def _parse_dim(d):
                if not d:
                    return None, None
                if isinstance(d, int):
                    return d, None
                m = re.search(r"(\d+)\s*[xX×]\s*(\d+)", str(d))
                if m:
                    return int(m.group(1)), int(m.group(2))
                return None, None
            w_str, h_str = _parse_dim(props.get("pixel_dimensions"))
            w_disp, h_disp = _parse_dim(props.get("display_dimensions"))
            entry["width"] = (
                props.get("video_pixel_width")
                or props.get("pixel_width")
                or w_str
                or w_disp
            )
            entry["height"] = (
                props.get("video_pixel_height")
                or props.get("pixel_height")
                or h_str
                or h_disp
            )
        tracks.append(entry)
    return tracks


def extract_audio_wav(src, stream_index, out_wav, duration=None, start=None):
    """抽取指定音轨为 16k 单声道 WAV（用于 AI 语言识别）。

    改进(v7)：支持 start 参数（起始秒数），用于跳过片头。
      - start: 起始秒数（如90，跳过前90秒片头）
      - duration: 抽取时长秒数（如90，从start开始抽取90秒）
    """
    cmd = [_find_bin("ffmpeg"), "-y", "-v", "error", "-i", src, "-map", f"0:{stream_index}",
           "-vn", "-ac", "1", "-ar", "16000"]
    if start:
        cmd += ["-ss", str(start)]
    if duration:
        cmd += ["-t", str(duration)]
    cmd.append(out_wav)
    rc, _, err = run(cmd, timeout=300, log_stage="TOOLS", label="ffmpeg-抽音轨")
    if rc != 0 or not os.path.exists(out_wav):
        raise CmdError(cmd, rc, err)


def extract_subtitle(src, stream_index, out_path, codec_hint=None):
    """抽取字幕轨道到文件（文本或 sup 均可，ffmpeg -c copy）。"""
    cmd = [_find_bin("ffmpeg"), "-y", "-v", "error", "-i", src, "-map", f"0:{stream_index}",
           "-c", "copy", out_path]
    rc, _, err = run(cmd, timeout=300, log_stage="TOOLS", label="ffmpeg-抽字幕")
    if rc != 0 or not os.path.exists(out_path):
        raise CmdError(cmd, rc, err)


def make_temp_path(suffix, base_dir=None):
    """生成一个临时文件路径（不创建文件）。"""
    fd, path = tempfile.mkstemp(suffix=suffix, dir=base_dir)
    os.close(fd)
    return path


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# OCR 工具函数（v21.2）：ffmpeg 抽帧 + PaddleOCR 文字识别（替代 Tesseract）
# ---------------------------------------------------------------------------

def extract_frames_with_subtitle(src_path, sub_stream_index, out_dir,
                                 config=None, temp_dir=None, sub_path=None):
    """用 ffmpeg 从视频中抽取关键帧并烧录指定字幕轨道到画面上（v21.2 简化版）。"""
    cfg = config or {}
    skip_sec = cfg.get("ocr_skip_seconds", 300)
    os.makedirs(out_dir, exist_ok=True)
    ffmpeg_exe = _find_bin("ffmpeg")
    output_pattern = os.path.join(out_dir, "frame_%04d.png")
    if sub_path and os.path.exists(sub_path):
        cmd = [
            ffmpeg_exe, "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=black:s=1920x1080",
            "-ss", str(skip_sec), "-t", "30",
            "-i", sub_path,
            "-filter_complex", "[0:v][1:s]overlay=shortest=1",
            "-r", "1", output_pattern,
        ]
        try:
            rc, _, _ = run(cmd, timeout=120, log_stage="TOOLS", label="ffmpeg-OCR抽帧")
            if rc == 0:
                frames = sorted(
                    f for f in os.listdir(out_dir)
                    if f.startswith("frame_") and f.endswith(".png"))
                return [os.path.join(out_dir, f) for f in frames]
        except Exception as e:
            logger.log(f"OCR抽帧异常: {e}", "TOOLS")
    return []


def ocr_image_with_paddle(image_paths, config=None):
    """用 PaddleOCR 对一批图片做文字识别，返回 (文本, 是否成功)。

    PaddleOCR ch 模型内置简体中文+英文混合识别，无需多语言组合。
    简繁判断由调用方（classify_subtitle_text）基于文本自身判断。
    兼容 v2 (PP-OCRv4) 和 v3+ (PP-OCRv5) 两个 API 版本。
    """
    cfg = config or {}
    min_len = int(cfg.get("ocr_min_text_len", 30))
    if not _init_paddle_ocr(cfg):
        logger.log("PaddleOCR 不可用，跳过 OCR", "TOOLS")
        return "", False
    parts = _paddle_ocr_batch(_PADDLE_OCR, image_paths)
    combined = "\n".join(parts).strip()
    preview = combined[:120].replace("\n", "↵") if combined else "(空)"
    logger.log(f"PaddleOCR 识别({len(combined)}B): {preview}", "TOOLS")
    if not combined or len(combined) < min_len:
        return "", False
    return combined, True
