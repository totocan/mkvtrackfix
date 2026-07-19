# -*- coding: utf-8 -*-
"""
字幕轨道语言识别（v13 异步双缓冲终极优化版）：
  - 完美兼容本地暂存路径机制：
    - 数据读取（mkvextract 提取、FFmpeg 图像字幕分帧）强制走 `src_path`（本地缓存），I/O 网络开销归零。
    - 启发式语义推断、同名外部字幕检索强制走 `orig_path`（原 NAS 路径），确保识别高精准度，解决全识别为“简中英双语”的 Bug。
"""
import os
import re
import tempfile
import subprocess
import shutil
import traceback
import hashlib

from . import lang_map, utils, logger

# 文本类字幕 codec -> 抽取扩展名
TEXT_CODEC_EXT = {
    "subrip": ".srt", "srt": ".srt",
    "ass": ".ass", "ssa": ".ssa",
    "webvtt": ".vtt", "vtt": ".vtt",
    "sami": ".smi", "smi": ".smi",
    "mov_text": ".srt", "text": ".txt",
    "microdvd": ".sub", "subrip2": ".srt",
}
# 图像类字幕（需 OCR）
IMAGE_CODECS = {"hdmv_pgs_subtitle", "hdmv/pgs", "hdmv pgs", "dvd_subtitle", "dvb_subtitle", "pgssub"}

# OCR 输出 file 大小下限（bytes）
_MIN_SRT_SIZE = 50


def _ext_for(codec):
    return TEXT_CODEC_EXT.get((codec or "").lower(), ".srt")


def _strip_tags(text):
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\{[^}]*\}", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    return text


def _extract_text(path):
    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
    except Exception:
        return ""
    if ext in (".srt", ".vtt"):
        lines = []
        for ln in raw.splitlines():
            s = ln.strip()
            if not s:
                continue
            if s.isdigit():
                continue
            if "-->" in s:
                continue
            lines.append(_strip_tags(s))
        return "\n".join(lines)
    if ext in (".ass", ".ssa"):
        texts = []
        for ln in raw.splitlines():
            if not ln.startswith("Dialogue:"):
                continue
            body = ln.split(":", 1)[1].strip()
            parts = body.split(",")
            text = parts[9] if len(parts) >= 10 else parts[-1]
            texts.append(_strip_tags(text))
        return "\n".join(texts)
    if ext == ".smi":
        return _strip_tags(re.sub(r"<(/?SYNC[^>]*)>", " ", raw, flags=re.I))
    return _strip_tags(raw)


def _log_to_ai_worker(message):
    """辅助函数：将字幕检测和 OCR 调试日志统一写入 logs/mmf_ai_worker.log"""
    try:
        from . import config
        log_dir = os.path.abspath(os.path.join(config.app_root(), "logs"))
        os.makedirs(log_dir, exist_ok=True)
        ai_log_path = os.path.join(log_dir, "mmf_ai_worker.log")
        with open(ai_log_path, "a", encoding="utf-8") as f:
            f.write(f"[{utils.time_str() if hasattr(utils, 'time_str') else 'INFO'}] {message}\n")
    except Exception:
        pass


def _clean_windows_path(path):
    """智能路径净化"""
    if not path:
        return path
    normalized = os.path.normpath(path)
    if path.startswith("//") or path.startswith("\\\\"):
        normalized = "\\\\" + normalized.lstrip("\\/")
    return normalized


def _safe_extract_subtitle(src_path, stream_index, tmp_out, codec, config):
    """
    【Mvktoolnix 强力抽取引擎】
    使用本地缓存 `src_path` 极速抽取，不再走网络 I/O 读 NAS。
    """
    mkvextract_path = "tools/mkvtoolnix/mkvextract.exe"
    if config and "ffmpeg_path" in config:
        ffmpeg_dir = os.path.dirname(config["ffmpeg_path"])
        possible_mkv = os.path.join(ffmpeg_dir, "mkvtoolnix", "mkvextract.exe")
        if os.path.exists(possible_mkv):
            mkvextract_path = possible_mkv

    clean_src = _clean_windows_path(src_path)
    clean_out = _clean_windows_path(tmp_out)

    cmd = [
        mkvextract_path,
        "tracks",
        clean_src,
        f"{stream_index}:{clean_out}"
    ]

    _log_to_ai_worker(f"[SUBTITLE_EXEC] 正在使用 mkvextract 执行本地缓存抽取: {' '.join(cmd)}")

    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         cwd=utils.app_root(), startupinfo=startupinfo)
    # v22: 超时从 config 读取，默认 180 秒（大文件 13GB+ 可能需加大）
    timeout = (config or {}).get("subtitle_extract_timeout", 180)
    try:
        stdout, stderr = p.communicate(timeout=timeout)
        rc = p.returncode
        if rc != 0:
            err_msg = stderr.decode('utf-8', errors='ignore') or stdout.decode('utf-8', errors='ignore')
            raise RuntimeError(f"mkvextract 进程退出码为 {rc}。错误详情:\n{err_msg}")
        _log_to_ai_worker(f"[SUBTITLE_SUCCESS] 成功抽取字幕流 0:{stream_index} -> {clean_out}")
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        raise TimeoutError("mkvextract 抽取字幕流超时已被中止。")
    except Exception as e:
        if p:
            p.kill()
            p.communicate()
        raise e


def _sample_ocr_frames(frame_paths, max_frames=30, log_callback=None):
    """从帧列表中采样最多 max_frames 帧用于 OCR。

    算法（v21.2）：
      1. 帧数 <= max_frames → 全部保留
      2. 否则先均匀采样到 60 帧
      3. 用 (文件大小, 中间4KB) hash 去重（避开 1KB 头部相似的 PNG 元数据问题）
      4. 去重后如仍 > max_frames，均匀缩到 max_frames
      注意：被丢弃的帧保留在磁盘上不删除（方便测试时排查 OCR 问题）。

    返回采样后的帧路径列表。
    """
    def _log(m):
        if log_callback:
            log_callback(m)
    
    if len(frame_paths) <= max_frames:
        return frame_paths
    
    total = len(frame_paths)
    
    # 步骤1: 均匀采样到 60 帧
    target_after_uniform = 60
    sampled = []
    if total > target_after_uniform:
        indices = set(int(i * (total - 1) / (target_after_uniform - 1)) 
                      for i in range(target_after_uniform))
        sampled = [frame_paths[i] for i in sorted(indices)]
    else:
        sampled = list(frame_paths)
    
    # 步骤2: 用 (文件大小, 中间4KB hash) 去重，比 1KB 头部更可靠
    hash_groups = {}  # (size, mid_hash) -> [path, ...]
    for fp in sampled:
        try:
            size = os.path.getsize(fp)
            # 读取文件中间 4KB（避开 PNG 头部元数据）
            seek_to = max(0, size // 2 - 2048)
            with open(fp, "rb") as f:
                f.seek(seek_to)
                chunk = f.read(4096)
            h = hashlib.md5(chunk).hexdigest() if chunk else ""
            key = (size, h)
        except Exception:
            key = (0, "")
        hash_groups.setdefault(key, []).append(fp)

    # 从每组取第一帧
    deduped = [paths[0] for paths in hash_groups.values()]
    
    n_dedup = len(deduped)
    _log(f"OCR帧采样: {total} → 均匀{target_after_uniform} → 去重{n_dedup}帧")
    
    # 步骤3: 如果去重后仍然超过 max_frames，均匀缩到 max_frames
    if n_dedup > max_frames:
        final_indices = set(int(i * (n_dedup - 1) / (max_frames - 1))
                           for i in range(max_frames))
        final_frames = []
        for i, fp in enumerate(deduped):
            if i in final_indices:
                final_frames.append(fp)
            # 其余帧保留在磁盘上（测试时方便排查 OCR 问题），不删除
        _log(f"OCR帧采样: 最终保留 {len(final_frames)} 帧")
        return final_frames
    
    return deduped


def _ocr_with_tesseract(src_path, sub_stream_index, config, temp_dir, sub_path=None,
                        segment_start=None):
    """智能间隔采样 OCR。去掉 shortest=1 问题。返回 (text, script) 或 (None, "unknown")。"""
    cfg = config or {}
    skip_sec = int(cfg.get("ocr_skip_seconds", 300))
    max_attempts = int(cfg.get("ocr_max_attempts", 4))
    min_len = int(cfg.get("ocr_min_text_len", 30))
    if segment_start is not None:
        attempt_starts = [segment_start]
    else:
        attempt_starts = [skip_sec + i * 30 for i in range(max_attempts)]
    per_attempt_duration = 30
    frame_dir = os.path.join(temp_dir, f"ocr_sub{sub_stream_index}")
    os.makedirs(frame_dir, exist_ok=True)
    ffmpeg_path = (config or {}).get("ffmpeg_path", "ffmpeg")
    clean_frame_dir = not cfg.get("debug_mode", False)
    for attempt_idx, ocr_seek in enumerate(attempt_starts):
        if clean_frame_dir:
            for old in os.listdir(frame_dir):
                fp = os.path.join(frame_dir, old)
                try: os.remove(fp)
                except: pass
        _log_to_ai_worker(f"[OCR] 尝试{attempt_idx+1}: {ocr_seek}s 持续{per_attempt_duration}s")
        if sub_path and os.path.exists(sub_path):
            sub_input = _clean_windows_path(sub_path)
            cmd = [
                ffmpeg_path, "-y",
                "-f", "lavfi", "-i", "color=c=black:s=1920x1080:r=1",
                "-ss", str(ocr_seek), "-t", str(per_attempt_duration),
                "-i", sub_input,
                "-filter_complex", "[0:v][1:s]overlay=shortest=1",
                os.path.join(frame_dir, "frame_%04d.png")
            ]
        else:
            clean_src = _clean_windows_path(src_path)
            cmd = [
                ffmpeg_path, "-y",
                "-f", "lavfi", "-i", "color=c=black:s=1920x1080:r=1",
                "-ss", str(ocr_seek), "-t", str(per_attempt_duration),
                "-i", clean_src,
                "-filter_complex",
                f"[0:v][1:{sub_stream_index}]overlay=shortest=1",
                os.path.join(frame_dir, "frame_%04d.png")
            ]
        _log_to_ai_worker(f"[OCR_EXEC] {' '.join(cmd)}")
        p = None
        frame_paths = []
        try:
            si = None
            if os.name == 'nt':
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,
                                 cwd=utils.app_root(), startupinfo=si)
            out, err = p.communicate(timeout=45)
            if p.returncode == 0:
                frame_paths = sorted(
                    os.path.join(frame_dir, f)
                    for f in os.listdir(frame_dir)
                    if f.startswith("frame_") and f.endswith(".png"))
                _log_to_ai_worker(f"[OCR] {ocr_seek}s → {len(frame_paths)}帧")
            else:
                e = (err.decode('utf-8','ignore') or out.decode('utf-8','ignore')).strip()
                el = [l for l in e.split('\n') if l.strip()]
                _log_to_ai_worker(f"[OCR_ERR] rc={p.returncode}: {el[-1][:200] if el else '?'}")
                continue
        except subprocess.TimeoutExpired:
            if 'p' in locals() and p: p.kill(); p.communicate()
            _log_to_ai_worker(f"[OCR_ERR] 尝试{ocr_seek}s 超时")
            continue
        if not frame_paths:
            _log_to_ai_worker(f"[OCR] {ocr_seek}s 无帧")
            continue
        sampled = _sample_ocr_frames(frame_paths, max_frames=30, log_callback=_log_to_ai_worker)
        text, _ = utils.ocr_image_with_rapid(sampled, config=config)
        script = None
        if text and len(text.strip()) >= min_len:
            if clean_frame_dir:
                try: shutil.rmtree(frame_dir, ignore_errors=True)
                except: pass
            return text, script
        _log_to_ai_worker(f"[OCR] {ocr_seek}s 文本({len(text or '')}B)不足，下一段")
    _log_to_ai_worker("[OCR] 4次尝试均无有效文本")
    if clean_frame_dir:
        try: shutil.rmtree(frame_dir, ignore_errors=True)
        except: pass
    return None, None

def detect(track, src_path, temp_dir, config, orig_path=None, existing_file=None):
    """
    识别单个字幕轨道。
    
    参数：
      - src_path: 本地的高速临时视频缓存文件（极速 I/O）
      - orig_path: 视频在 NAS 上的原始文件路径（用于高精确度的启发式同名推断与外部字幕查找）
    """
    codec = track.codec.lower()
    is_image = codec in IMAGE_CODECS
    logger.log(f"    字幕#{track.track_id} codec={codec} is_image={is_image}", "PIPELINE")
    
    # 路径分流：
    # 读视频数据抽取字幕，强制使用本地缓存 `src_path`
    # 寻找外部同名文件、运行语言学启发推断，强制使用 `orig_path`
    metadata_path = orig_path if orig_path else src_path

    # v21.2: 用 track_id 命名字幕文件（与 OCR 目录对齐：sub_{id}.sup <-> ocr_sub{id}/）
    ext = _ext_for(codec) if not is_image else ".sup"
    tmp_out = existing_file
    if not tmp_out or not os.path.exists(tmp_out):
        tmp_out = os.path.join(temp_dir, f"sub_{track.track_id}{ext}")
        try:
            # 核心：mkvextract 抽取走 src_path 本地高速文件
            _safe_extract_subtitle(src_path, track.stream_index, tmp_out, codec, config)
        except Exception as e:
            err_detail = traceback.format_exc()
            _log_to_ai_worker(
                f"[SUBTITLE_DETECT_FAIL] 字幕#{track.track_id} 本地抽取失败!"
                f"\n错误摘要: {str(e)}\n详细堆栈:\n{err_detail}")

            # 抽取失败 → 走启发式推断
            inferred_iso, inferred_name, conf, source = \
                lang_map.heuristic_infer_language(track, metadata_path)

            reason_brief = str(e).split('\n')[0][:50]
            if inferred_iso != "und":
                logger.log(f"字幕#{track.track_id} 抽取失败，启发式推断: "
                           f"{inferred_name}({inferred_iso}) "
                           f"conf={conf} source={source}", "PIPELINE")
                kind = "unknown"
                if inferred_iso in ("cmn", "chi", "zho"):
                    kind = "chinese_simplified"
                elif inferred_iso == "yue":
                    kind = "chinese_simplified"
                elif inferred_iso == "eng":
                    kind = "english"
                return {
                    "iso": inferred_iso, "zh": inferred_name,
                    "en": lang_map.lang_info_by_iso(inferred_iso).get("en", inferred_iso),
                    "kind": kind, "confidence": conf, "remove": False,
                    "note": f"字幕抽取失败(原因:{reason_brief})，启发式推断为{inferred_name}(来源:{source})"
                }
            return {"iso": "und", "zh": "未知", "en": "Unknown",
                    "kind": "unknown", "confidence": 0.0, "remove": False,
                    "note": f"抽取失败且无法推断: {reason_brief}"}

    if is_image:
        # 全帧单次提取 OCR（不使用 -ss 分段，避免跳跃段无字幕帧）
        ocr_res = _ocr_with_tesseract(
            src_path, track.stream_index, config, temp_dir, sub_path=tmp_out)
        if ocr_res is None:
            text, script = None, "unknown"
        else:
            text, script = ocr_res
        # OCR 文本日志预览（对排查 Tesseract 识别质量至关重要）
        if text:
            preview = text[:80].replace("\n", "↵")
            logger.log(f"    字幕#{track.track_id} OCR 文本预览({len(text)}B): {preview}",
                       "PIPELINE")
        if not text:
            inferred_iso, inferred_name, conf, source = \
                lang_map.heuristic_infer_language(track, metadata_path)
            if inferred_iso != "und":
                logger.log(f"字幕#{track.track_id} OCR失败，启发式推断: "
                           f"{inferred_name}({inferred_iso}) "
                           f"conf={conf} source={source}", "PIPELINE")
                kind = "chinese_simplified" if inferred_iso in ("cmn","chi","zho","yue") else \
                       "english" if inferred_iso == "eng" else "unknown"
                return {
                    "iso": inferred_iso, "zh": inferred_name,
                    "en": lang_map.lang_info_by_iso(inferred_iso).get("en", inferred_iso),
                    "kind": kind, "confidence": conf, "remove": False,
                    "note": f"OCR失败，启发式推断为{inferred_name}(来源:{source})",
                    "ocr_failed": True,
                }
            return {"iso": "und", "zh": "图像字幕(未识别)",
                    "en": "Image subtitle (unrecognized)",
                    "kind": "unknown", "confidence": 0.0, "remove": False,
                    "note": "OCR(Tesseract)失败且无法推断，已保留原样",
                    "ocr_failed": True}
    else:
        text = _extract_text(tmp_out)
        script = None  # 文本字幕直接读内容，繁简由 classify 自行判定

    if not text or not text.strip():
        inferred_iso, inferred_name, conf, source = \
            lang_map.heuristic_infer_language(track, metadata_path)
        if inferred_iso != "und":
            return {
                "iso": inferred_iso, "zh": inferred_name,
                "en": lang_map.lang_info_by_iso(inferred_iso).get("en", inferred_iso),
                "kind": "unknown", "confidence": conf, "remove": False,
                "note": f"字幕内容为空，启发式推断为{inferred_name}(来源:{source})"
            }
        return {"iso": "und", "zh": "空字幕", "en": "Empty subtitle",
                "kind": "unknown", "confidence": 0.0, "remove": False,
                "note": "字幕内容为空且无法推断，已保留"}

    # 成功提取到了文本，使用文本分类引擎识别真实语言类别
    res = lang_map.classify_subtitle_text(text, script_hint=None)
    res["remove"] = (res.get("kind") == "chinese_traditional")
    res["note"] = ""
    if res["remove"]:
        res["note"] = "繁体中文字幕，按策略移除"
    # 保存 OCR 原始文本前 300 字符供 UI tooltip 预览
    ocr_preview = text[:300].strip()
    res["ocr_text"] = ocr_preview
    return res


    # v22: 字幕提取与检测分离 — 仅提取，不检测
def extract_only(track, src_path, temp_dir, config):
    """提取单个字幕轨道到本地文件。返回提取后的文件路径，失败返回 None。"""
    codec = track.codec.lower()
    is_image = codec in IMAGE_CODECS
    ext = _ext_for(codec) if not is_image else ".sup"
    tmp_out = os.path.join(temp_dir, f"sub_{track.track_id}{ext}")
    if os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 0:
        return tmp_out
    try:
        _safe_extract_subtitle(src_path, track.stream_index, tmp_out, codec, config)
        if os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 0:
            return tmp_out
    except Exception:
        pass
    return None


# v22: 字幕提取与检测分离 — 对已提取的文件做 OCR + 语言检测
def extract_all(tracks, src_path, temp_dir, config):
    """一次性提取所有字幕轨道，单次 mkvextract 调用，只读一次源文件。

    参数：
      tracks  — 全部轨道列表（自动过滤 subtitle 类型）
      src_path — 源视频路径（本地缓存或 NAS）
      temp_dir  — 临时工作目录（每个视频各自的 tmp/N/temp/）
      config   — 配置字典

    返回：
      {track_id: 提取文件路径, ...} 或 {}（批量失败时返回空，由上游逐条回退）
    """
    from . import config as cfg_mod
    mkvextract_path = "tools/mkvtoolnix/mkvextract.exe"
    if config and "ffmpeg_path" in config:
        ffmpeg_dir = os.path.dirname(config["ffmpeg_path"])
        possible_mkv = os.path.join(ffmpeg_dir, "mkvtoolnix", "mkvextract.exe")
        if os.path.exists(possible_mkv):
            mkvextract_path = possible_mkv

    track_specs = []
    result = {}  # track_id -> extracted path

    for t in tracks:
        if t.track_type != "subtitle":
            continue
        codec = t.codec.lower()
        is_image = codec in IMAGE_CODECS
        ext = _ext_for(codec) if not is_image else ".sup"
        tmp_out = os.path.join(temp_dir, f"sub_{t.track_id}{ext}")
        if os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 0:
            result[t.track_id] = tmp_out
            continue
        track_specs.append(f"{t.stream_index}:{_clean_windows_path(tmp_out)}")
        result[t.track_id] = tmp_out

    if not track_specs:
        return result  # 全部已存在或没有字幕轨道

    clean_src = _clean_windows_path(src_path)
    cmd = [mkvextract_path, "tracks", clean_src] + track_specs

    _log_to_ai_worker(f"[SUBTITLE_BATCH_EXEC] 批量提取字幕: {' '.join(cmd)}")

    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    timeout = (config or {}).get("subtitle_extract_timeout", 180)
    # 批量提取超时适当放大（轨道数 × 单轨超时）
    batch_timeout = timeout * max(1, len(track_specs))

    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             cwd=cfg_mod.app_root() if hasattr(cfg_mod, 'app_root') else None,
                             startupinfo=startupinfo)
        stdout, stderr = p.communicate(timeout=batch_timeout)
        rc = p.returncode
        if rc != 0:
            err_msg = stderr.decode('utf-8', errors='ignore') or stdout.decode('utf-8', errors='ignore')
            _log_to_ai_worker(f"[SUBTITLE_BATCH_FAIL] 批量提取失败 rc={rc}: {err_msg[:200]}")
            return {}  # 批量失败，让上游逐条回退
        _log_to_ai_worker(f"[SUBTITLE_BATCH_SUCCESS] 批量提取完成: {len(track_specs)} 条轨道")
        # 验证每个提取文件存在
        valid = {}
        for track_id, path in result.items():
            if os.path.exists(path) and os.path.getsize(path) > 0:
                valid[track_id] = path
        return valid
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        _log_to_ai_worker(f"[SUBTITLE_BATCH_TIMEOUT] 批量提取超时({batch_timeout}s)")
        return {}
    except Exception as e:
        _log_to_ai_worker(f"[SUBTITLE_BATCH_ERR] 批量提取异常: {e}")
        if 'p' in locals() and p:
            p.kill()
            p.communicate()
        return {}


def detect_from_file(track, extracted_path, temp_dir, config, orig_path=None):
    """
    对已提取的字幕文件做 OCR + 语言检测。
    返回与 detect() 相同的 dict。
    通过 existing_file 参数跳过内部提取步骤。
    """
    return detect(track, extracted_path, temp_dir, config,
                  orig_path=orig_path, existing_file=extracted_path)