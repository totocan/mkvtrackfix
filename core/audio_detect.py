# -*- coding: utf-8 -*-
"""
音轨语言 AI 识别（v17 终极绿化版）。

特性(v17)：
  - 核心采样 OCR 引擎：注入 `_ocr_sample_subtitle`，支持通过 FFmpeg 采样实现无阻塞识别。
  - 一键包自适应：自动检索项目目录下 `tools/tesseract/tesseract.exe`，无需外部安装。
  - 网络审计流：完整保留豆瓣刮削监控与异常捕获。
  - 动态防御：如未检测到 OCR 环境，程序自动降级而非崩溃。
"""
import os
import sys
import threading
import time
import traceback
import subprocess
import tempfile
import shutil
from collections import defaultdict

# ================= 动态防御性 OCR 模块 =================
# 自动寻找项目根目录下的 tools/tesseract 以实现免安装部署
try:
    from . import config
    app_root = os.path.abspath(config.app_root())
    tesseract_cmd = os.path.join(app_root, "tools", "tesseract", "tesseract.exe")

    import pytesseract
    from PIL import Image
    
    # 强制劫持 tesseract 执行路径
    if os.path.exists(tesseract_cmd):
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        OCR_AVAILABLE = True
    else:
        # 如果未找到本地工具，尝试检查系统环境变量，否则禁用
        OCR_AVAILABLE = False
except ImportError:
    pytesseract = None
    Image = None
    OCR_AVAILABLE = False

# ================= 系统优化与补丁 =================
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

from . import lang_map, logger, ai_worker

# ================= 豆瓣网络审计劫持 =================
try:
    import requests
    _old_get = requests.get
    def _audited_requests_get(url, *args, **kwargs):
        if "douban" in str(url).lower() or "api" in str(url).lower():
            logger.log(f"[DOUBAN_HTTP_REQ] 正在发起豆瓣元数据联网刮削 -> URL: {url}", "PIPELINE")
            t_start = time.time()
            try:
                resp = _old_get(url, *args, **kwargs)
                logger.log(f"[DOUBAN_HTTP_RESP] 豆瓣联网响应成功 | 状态码: {resp.status_code} | 耗时: {time.time()-t_start:.2f}s", "PIPELINE")
                return resp
            except Exception as ree:
                logger.log(f"[DOUBAN_HTTP_ERR] 豆瓣联网请求遭遇网络溃决! 原因: {ree}", "PIPELINE")
                raise
        return _old_get(url, *args, **kwargs)
    requests.get = _audited_requests_get
except Exception as ne:
    logger.log(f"注入豆瓣网络审计流失败: {ne}", "SYSTEM")

# ================= 采样 OCR 核心引擎 =================
def _ocr_sample_subtitle(video_path, stream_index, sample_ts=300):
    """
    【采样 OCR 核心】本地化抽帧+OCR。
    针对 Windows 绝对路径及 UNC 网络路径（如 //192.168.x.x）进行了 FFmpeg subtitles 滤镜路径安全转义处理。
    """
    if not OCR_AVAILABLE:
        logger.log(f"[OCR_SKIP] OCR 环境未就绪，跳过采样。", "AI")
        return ""
    
    tmp_img_dir = tempfile.mkdtemp(prefix="mmf_ocr_")
    try:
        # FFmpeg subtitles 滤镜转义黄金法则：
        # 1. 反斜杠转为正斜杠： 'C:\path\to\file.mkv' -> 'C:/path/to/file.mkv'
        # 2. 对冒号进行转义： 'C:/path/to/file.mkv' -> 'C\:/path/to/file.mkv'
        # 3. 对单引号进行额外处理避免包裹冲突
        safe_path = video_path.replace('\\', '/')
        safe_path = safe_path.replace(':', '\\:')
        safe_path = safe_path.replace("'", "'\\\\''")

        # FFmpeg 采样命令：指定时间、指定流、烧录、截帧
        cmd = [
            'ffmpeg', '-y', '-ss', str(sample_ts),
            '-i', video_path,
            '-vf', f"subtitles='{safe_path}':si={stream_index}",
            '-vframes', '3', '-q:v', '2',
            os.path.join(tmp_img_dir, "frame_%03d.jpg")
        ]
        
        # 运行采样
        subprocess.run(cmd, check=True, timeout=15, capture_output=True)
        
        full_text = []
        for f in os.listdir(tmp_img_dir):
            if f.endswith(".jpg"):
                text = pytesseract.image_to_string(Image.open(os.path.join(tmp_img_dir, f)), lang='chi_sim+eng')
                full_text.append(text)
        
        return " ".join(full_text)
    except Exception as e:
        logger.log(f"[OCR_SAMPLE] 采样识别异常: {e}", "AI")
        return ""
    finally:
        _debug_mode = False
        try:
            from . import config as _cfg
            _debug_mode = _cfg.load().get("debug_mode", False)
        except: pass
        if not _debug_mode:
            shutil.rmtree(tmp_img_dir, ignore_errors=True)

# ================= 核心模型与调度逻辑 =================
_MIN_MODEL_BYTES = 100 * 1024 * 1024
USE_SUBPROCESS = True
_detector = ai_worker.AIDetector()
_MODEL = None
_MODEL_SIZE = None
_MODEL_LOCK = threading.Lock()

def _record_ai_crash(exc):
    try:
        from . import config
        log_dir = os.path.abspath(os.path.join(config.app_root(), "logs"))
        os.makedirs(log_dir, exist_ok=True)
        ai_log_path = os.path.join(log_dir, "mmf_ai_worker.log")
        with open(ai_log_path, "a", encoding="utf-8") as f:
            f.write(f"\n[AI DIAGNOSTIC] 捕获到 AI 子进程异常 | {str(exc)}\n")
            traceback.print_exc(file=f)
    except Exception:
        pass

def _local_model_path(model_size):
    from . import config
    root_dir = os.path.abspath(config.app_root())
    d = os.path.join(root_dir, "models", model_size)
    if os.path.isdir(d):
        config_file = os.path.join(d, "config.json")
        if not os.path.exists(config_file) or os.path.getsize(config_file) == 0:
            return None
        for fn in ("model.bin", "model.int8.bin"):
            p = os.path.join(d, fn)
            if os.path.exists(p) and os.path.getsize(p) >= _MIN_MODEL_BYTES:
                return d
    return None

def _load_model(model_size="medium", device="cpu", compute_type="int8", cpu_threads=0):
    global _MODEL, _MODEL_SIZE
    with _MODEL_LOCK:
        if _MODEL is not None and _MODEL_SIZE == model_size: return _MODEL
        from faster_whisper import WhisperModel
        local = _local_model_path(model_size)
        model_arg = local if local else model_size
        _MODEL = WhisperModel(model_arg, device=device, compute_type=compute_type, cpu_threads=cpu_threads or 0)
        _MODEL_SIZE = model_size
    return _MODEL

def _decide_chinese(title_hint, config):
    cfg = (config or {})
    forced = cfg.get("zh_audio_as", "cmn")
    if forced in ("yue", "cmn"): return forced
    hint = (title_hint or "").lower()
    if any(k in hint for k in ("粤", "cantonese", "yue", "gd")): return "yue"
    return "cmn"

_LANG_PRIORITY = {"cmn": 1, "chi": 1, "zho": 1, "yue": 2, "eng": 3}
_DOMINANT_THRESHOLD = 0.6

def _detect_with_model(model, wav_path, title_hint, cfg):
    logger.log(f"AI 识别中 (wav={os.path.basename(wav_path)}) ...", "AI")
    t0 = time.time()
    segments, info = model.transcribe(wav_path, language=None, beam_size=1, best_of=1, without_timestamps=True, vad_filter=False)
    
    lang_duration = defaultdict(float)
    total_duration = 0.0
    for seg in segments:
        seg_lang = getattr(seg, 'language', (info.language or "und")).lower()
        if seg_lang == "zh": seg_lang = _decide_chinese(title_hint, cfg)
        seg_dur = max((getattr(seg, 'end', 0) - getattr(seg, 'start', 0)), 0.1)
        lang_duration[seg_lang] += seg_dur
        total_duration += seg_dur

    if total_duration < 0.5:
        iso1 = (info.language or "und").lower()
        if iso1 == "zh": iso1 = _decide_chinese(title_hint, cfg)
        info_out = lang_map.lang_info(iso1, media_type="audio")
        info_out.update({"iso1": iso1, "prob": 0, "kind": "audio", "dominant_ratio": 1.0})
        return info_out

    lang_ratio = {l: d/total_duration for l, d in lang_duration.items()}
    dominant_lang = max(lang_ratio, key=lang_ratio.get)
    iso1 = dominant_lang
    info_out = lang_map.lang_info(iso1, media_type="audio")
    info_out.update({"iso1": iso1, "prob": 1.0, "kind": "audio", "dominant_ratio": round(lang_ratio[dominant_lang], 3)})
    
    logger.log(f"AI 识别结果: {iso1} ({info_out.get('zh')}) | 耗时: {time.time()-t0:.1f}s", "AI")
    return info_out

def _detect_inproc(wav_path, title_hint=None, config=None):
    cfg = config or {}
    model = _load_model(cfg.get("model_size", "medium"), cfg.get("device", "cpu"), cfg.get("compute_type", "int8"), int(cfg.get("cpu_threads", 0)))
    return _detect_with_model(model, wav_path, title_hint, cfg)

def detect(wav_path, title_hint=None, config=None):
    if USE_SUBPROCESS:
        try: return _detector.detect(wav_path, title_hint, config)
        except Exception as e:
            _record_ai_crash(e)
            raise
    return _detect_inproc(wav_path, title_hint, config)

def shutdown():
    try: _detector.shutdown()
    except Exception: pass