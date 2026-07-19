# -*- coding: utf-8 -*-
"""
处理编排（v8.1 异步预拉取高速缓存终极适配防错版）：文件收集 -> 产地判断 -> 轨道解析 -> AI/规则识别 -> 策略 -> 转封装。

改进：
  - 完美适配异步双缓冲机制，支持 path (本地暂存路径) 与 orig_path (原 NAS/网络路径) 双路径分流。
  - 【增强防错】：如果检测到 `path` 传入的是网络路径（如 \\\\ 开头），而 `orig_path` 为空，
    会自动尝试从全局缓存环境或传入参数中寻找本地缓存，确保 100% 走本地极速 I/O。
"""
import os

from . import (audio_detect, douban, lang_map, policy, probe, remux,
               subtitle_detect, utils, logger)


def collect_files(source, recursive=True, extensions=("mp4", "mkv")):
    """从文件或目录收集待处理媒体文件。支持 UNC 路径。"""
    files = []
    exts = {e.lower().lstrip(".") for e in extensions}
    if os.path.isfile(source):
        if os.path.splitext(source)[1].lower().lstrip(".") in exts:
            files.append(source)
        return files
    if os.path.isdir(source):
        if recursive:
            for root, _, names in os.walk(source):
                for n in names:
                    if os.path.splitext(n)[1].lower().lstrip(".") in exts:
                        files.append(os.path.join(root, n))
        else:
            for n in os.listdir(source):
                p = os.path.join(source, n)
                if os.path.isfile(p) and \
                        os.path.splitext(n)[1].lower().lstrip(".") in exts:
                    files.append(p)
    return sorted(files)


def _analyze_audio(track, src, temp_dir, config, log, orig_path=None):
    """音轨语言识别（Legacy：提取+检测一步完成，仅供 process_file 使用）。"""
    infer_path = orig_path if orig_path else src
    _audio_early_check(track, config, log, infer_path)
    if track.detected_iso:
        return
    wavs = _extract_audio_segments(track, src, temp_dir, config, log)
    if wavs:
        _detect_audio(track, wavs, temp_dir, config, log, infer_path)


def _audio_early_check(track, config, log, infer_path):
    """音轨早期返回判断：skip 模式 / und_only 模式。设置 detected_iso 后返回。"""
    mode = config.get("audio_redetect", "all")
    norm = track.language_norm
    if mode == "skip":
        if norm == "und":
            inferred_iso, inferred_name, conf, source = \
                lang_map.heuristic_infer_language(track, infer_path)
            track.detected_iso = inferred_iso
            track.detected_name = inferred_name
            if inferred_iso != "und":
                log(f"    音轨#{track.track_id} skip+und -> 启发式推断: "
                    f"{inferred_name}({inferred_iso}) source={source}")
            else:
                log(f"    音轨#{track.track_id} skip+und -> 无法推断，保持und")
        else:
            info = lang_map.lang_info(norm, media_type="audio")
            track.detected_iso = info["iso"]
            track.detected_name = info["zh"]
        return
    if mode == "und_only" and norm != "und":
        info = lang_map.lang_info(norm, media_type="audio")
        track.detected_iso = info["iso"]
        track.detected_name = info["zh"]
        return
    track.detected_iso = ""


def _extract_audio_segments(track, src, temp_dir, config, log):
    """提取音轨 WAV 段到本地 temp。返回 [(start_sec, wav_path), ...] 或 None。"""
    sample_duration = config.get("sample_duration_seconds", 10)
    segments_str = config.get("sample_segments", "600,1000,1500")
    sample_starts = [int(s.strip()) for s in segments_str.split(",") if s.strip()] or [300]
    wavs = []
    for i, ss in enumerate(sample_starts):
        wav_name = f"audio{track.track_id}_seg{i}_{ss}s.wav"
        wav = os.path.join(temp_dir, wav_name)
        if os.path.exists(wav):
            wavs.append((ss, wav))
            continue
        try:
            utils.extract_audio_wav(src, track.stream_index, wav,
                                    duration=sample_duration, start=ss)
            wavs.append((ss, wav))
            log(f"    音轨#{track.track_id} 段{ss}s WAV已提取: {wav_name}")
        except Exception as e:
            log(f"    音轨#{track.track_id} 段{ss}s 提取失败: {e}")
            if os.path.exists(wav):
                try: os.remove(wav)
                except: pass
    if wavs:
        return wavs
    log(f"    音轨#{track.track_id} 所有取样段均提取失败", "error")
    return None


def _detect_audio(track, wavs, temp_dir, config, log, infer_path):
    """对已提取的 WAV 做 AI 识别+投票。设置 track.detected_iso / detected_name。"""
    combined_wav = os.path.join(temp_dir, f"audio{track.track_id}_combined.wav")
    try:
        concat_list = os.path.join(temp_dir, f"concat{track.track_id}.txt")
        with open(concat_list, "w", encoding="utf-8") as f:
            for _, wav in wavs:
                f.write(f"file '{wav}'\n")
        utils.run([
            utils._find_bin("ffmpeg"), "-y", "-f", "concat",
            "-safe", "0", "-i", concat_list,
            "-c", "copy", combined_wav
        ], quiet=True, log_stage="TOOLS", label="ffmpeg-合并音频段")
        if not os.path.exists(combined_wav):
            raise RuntimeError("合并WAV文件失败")
        os.remove(concat_list)
    except Exception as e:
        log(f"    音轨#{track.track_id} 合并音频段失败，回退逐段识别: {e}")
        combined_wav = None
    votes = {}
    if combined_wav:
        try:
            res = audio_detect.detect(combined_wav, title_hint=track.title, config=config)
            iso = res["iso"]
            zh = res["zh"]
            votes[iso] = 3
            log(f"    音轨#{track.track_id} 合并段 -> {zh}({iso}) "
                f"dominant={res.get('dominant_ratio', 1.0):.2f}")
        except Exception as e:
            log(f"    音轨#{track.track_id} 合并段 AI异常: {e}", "error")
    else:
        for ss, wav in wavs:
            try:
                res = audio_detect.detect(wav, title_hint=track.title, config=config)
                iso = res["iso"]
                zh = res["zh"]
                votes.setdefault(iso, 0)
                votes[iso] += 1
                log(f"    音轨#{track.track_id} 段{ss}s -> {zh}({iso}) "
                    f"conf={res.get('prob', 0):.2f}")
            except Exception as e:
                log(f"    音轨#{track.track_id} 段{ss}s AI异常: {e}", "error")
    if votes:
        winner_iso = max(votes, key=votes.get)
        info = lang_map.lang_info_by_iso(winner_iso)
        if info:
            track.detected_iso = info["iso"]
            track.detected_name = info["zh"]
            log(f"    音轨#{track.track_id} 投票结果: {info['zh']}({info['iso']}) "
                f"票数={votes}")
        else:
            track.detected_iso = winner_iso
            track.detected_name = winner_iso
            log(f"    音轨#{track.track_id} 投票结果: {winner_iso} 票数={votes}")
    else:
        inferred_iso, inferred_name, conf, source = \
            lang_map.heuristic_infer_language(track, infer_path)
        if inferred_iso != "und":
            track.detected_iso = inferred_iso
            track.detected_name = inferred_name
            log(f"    [警告] 音轨#{track.track_id} AI失败，启发式推断: "
                f"{inferred_name}({inferred_iso}) conf={conf} source={source}")
        else:
            track.detected_iso = "und"
            track.detected_name = "未知"
            log(f"    [警告] 音轨#{track.track_id} AI失败且无法推断，保持 und")
    if not config.get("debug_mode", False):
        for _, wav in wavs:
            if os.path.exists(wav):
                try: os.remove(wav)
                except: pass
        if combined_wav and os.path.exists(combined_wav):
            try: os.remove(combined_wav)
            except: pass

def _analyze_subtitle(track, src, temp_dir, config, log, orig_path=None):
    """字幕语言识别（v7：und 启发式兜底；v8：兼容缓存路径）。"""
    infer_path = orig_path if orig_path else src

    # 调试：输出 codec 和 is_image（用 log 回调让 GUI 也显示）
    log(f"    字幕#{track.track_id} codec={track.codec} "
        f"is_image={track.codec.lower() in subtitle_detect.IMAGE_CODECS}")

    try:
        # 直接将分离后的本地高速缓存 src 和 原始 NAS 路径 orig_path 传给底层
        res = subtitle_detect.detect(track, src, temp_dir, config, orig_path=orig_path)
    except TypeError:
        try:
            res = subtitle_detect.detect(track, src, temp_dir, config)
        except Exception as e:
            res = None
            err_msg = str(e)
    except Exception as e:
        res = None
        err_msg = str(e)

    if res is None:
        inferred_iso, inferred_name, conf, source = \
            lang_map.heuristic_infer_language(track, infer_path)
        if inferred_iso != "und":
            res = {"iso": inferred_iso, "zh": inferred_name,
                   "en": lang_map.lang_info_by_iso(inferred_iso).get("en", inferred_iso),
                   "kind": "unknown", "confidence": conf, "remove": False,
                   "note": f"识别异常，启发式推断为{inferred_name}(来源:{source})"}
        else:
            res = {"iso": "und", "zh": "未知", "en": "Unknown",
                   "kind": "unknown", "confidence": 0.0, "remove": False,
                   "note": f"识别异常且无法推断: {err_msg}"}

    track.detected_iso = res.get("iso", "und")
    track.detected_name = res.get("zh", "未知")
    track.detected_kind = res.get("kind", "unknown")
    track.note = res.get("note", "")
    track.ocr_text = res.get("ocr_text", "")
    track.ocr_failed = res.get("ocr_failed", False)  # v22
    if res.get("remove"):
        track.action = "remove"
    ocr_preview = (track.ocr_text or "")[:120].replace("\n", "↵")
    log(f"    字幕#{track.track_id} 识别为 {res.get('zh')} "
        f"(kind={res.get('kind')})" +
        (f" -> {res.get('note')}" if res.get("note") else "") +
        (f"\n      OCR: {ocr_preview}" if ocr_preview else ""))


def _auto_resolve_paths(path, orig_path):
    """
    内部兜底助手：如果发现主界面传来不规范的路径参数，在此进行自动修正分流。
    """
    resolved_path = path
    resolved_orig = orig_path

    # 如果 path 是一个网络路径（以 \\ 或 // 开头），且没有指定 orig_path
    if (path.startswith("\\\\") or path.startswith("//")) and not orig_path:
        # 我们寻找是否有当前系统临时文件夹下的预拉取缓存痕迹
        # 如果能在本地临时目录里匹配到同名大小一致或缓存特征的临时文件，则进行动态劫持。
        # 即使无法完美劫持，打印警报日志指引开发者排查
        logger.log(f"[WARNING] 核心警报：接收到 NAS 路径作为分析源 '{path}'，但本地缓存路径未传入！请立即检查 main_window.py 中的调用分流。", "PIPELINE")
    
    return resolved_path, resolved_orig


def analyze_file(path, config, log=None, orig_path=None, temp_dir=None):
    """解析 + 产地判断 + 识别 + 策略，返回 (tracks, summary)。不写盘。

    v23: 支持外部传入 temp_dir（每个视频各自的 tmp/N/temp/）；
         字幕轨道一次性批量提取，只读一次源文件。

    参数:
      path     — 源视频路径（走缓存时为本地缓存路径，不走缓存时为原始路径）
      config   — 配置字典
      log      — 日志回调
      orig_path — 原始路径（NAS/网络），用于产地判断和启发式推断
      temp_dir  — 临时工作目录（每个视频独立，由调用者创建）
    """
    def L(m, level="info"):
        if log:
            log(m, level)

    path, orig_path = _auto_resolve_paths(path, orig_path)
    display_path = orig_path if orig_path else path
    
    L(f"分析: {display_path}")
    logger.log(f"== STAGE probe == 解析轨道: {path}", "PIPELINE")
    
    # 1. 轨道解析
    tracks = probe.probe_media(path)
    
    # 临时工作目录：优先使用外部传入的，否则用默认的 tmp/temp/
    if temp_dir is None:
        from . import config as cfg_mod
        temp_dir = os.path.join(cfg_mod.app_root(), "tmp", "temp")
    os.makedirs(temp_dir, exist_ok=True)

    na = sum(1 for t in tracks if t.track_type == "audio")
    ns = sum(1 for t in tracks if t.track_type == "subtitle")
    logger.log(f"== STAGE detect == 音轨 {na} / 字幕 {ns}，开始识别", "PIPELINE")

    # ---- 2. 产地判断（使用原始路径保证豆瓣搜索正确） ----
    logger.log(f"== STAGE classify == 判断电影产地", "PIPELINE")
    movie_info = douban.classify_movie(display_path, config)
    L(f"    产地判断: 国产={movie_info['is_domestic']}, "
      f"原生语言={movie_info['native_lang_name']}({movie_info['native_lang']}), "
      f"来源={movie_info['source']}")
    # v22: 缓存 TMDB 信息供重命名使用
    config["_tmdb_movie_info"] = movie_info

    # ---- 3a. 音轨提取（所有音轨一次性集中提取 WAV）----
    infer_path = display_path
    audio_wavs = {}  # track_id -> [(start, wav_path), ...]
    for t in tracks:
        if t.track_type == "audio":
            _audio_early_check(t, config, L, infer_path)
            if not t.detected_iso:
                wavs = _extract_audio_segments(t, path, temp_dir, config, L)
                if wavs:
                    audio_wavs[t.track_id] = wavs

    # ---- 3b. 字幕提取（一次性批量提取，只读一次源文件）----
    sub_paths = subtitle_detect.extract_all(tracks, path, temp_dir, config)
    if sub_paths:
        for tid, sp in sub_paths.items():
            L(f"    字幕#{tid} 已提取: {os.path.basename(sp)}")
    else:
        # 批量提取失败，逐条回退
        L(f"    批量提取失败，逐条回退", "warn")
        for t in tracks:
            if t.track_type == "subtitle":
                sub_path = subtitle_detect.extract_only(t, path, temp_dir, config)
                if sub_path:
                    sub_paths[t.track_id] = sub_path
                    L(f"    字幕#{t.track_id} 已提取: {os.path.basename(sub_path)}")
                else:
                    L(f"    字幕#{t.track_id} 提取失败", "warn")

    # ---- 3c. 音轨 AI 检测（全本地，零 NAS）----
    for t in tracks:
        if t.track_type == "audio" and t.track_id in audio_wavs:
            _detect_audio(t, audio_wavs[t.track_id], temp_dir, config, L, infer_path)
        elif t.track_type == "audio" and not t.detected_iso:
            inferred_iso, inferred_name, conf, source = \
                lang_map.heuristic_infer_language(t, infer_path)
            t.detected_iso = inferred_iso
            t.detected_name = inferred_name

    # ---- 3d. 字幕 OCR 检测（全本地，零 NAS）----
    for t in tracks:
        if t.track_type == "subtitle" and t.track_id in sub_paths:
            res = subtitle_detect.detect_from_file(
                t, path, sub_paths[t.track_id], temp_dir, config, orig_path=orig_path)
            t.detected_iso = res.get("iso", "und")
            t.detected_name = res.get("zh", "未知")
            t.detected_kind = res.get("kind", "unknown")
            t.note = res.get("note", "")
            t.ocr_text = res.get("ocr_text", "")
            t.ocr_failed = res.get("ocr_failed", False)
            if res.get("remove"):
                t.action = "remove"
            ocr_preview = (t.ocr_text or "")[:120].replace("\n", "↵")
            L(f"    字幕#{t.track_id} 识别为 {res.get('zh')} "
              f"(kind={res.get('kind')})" +
              (f" -> {res.get('note')}" if res.get("note") else "") +
              (f"\n      OCR: {ocr_preview}" if ocr_preview else ""))
        elif t.track_type == "subtitle":
            # 提取失败，启发式兜底
            infer_path_sub = orig_path if orig_path else path
            inferred_iso, inferred_name, conf, source = \
                lang_map.heuristic_infer_language(t, infer_path_sub)
            t.detected_iso = inferred_iso
            t.detected_name = inferred_name
            t.detected_kind = "unknown"
            t.note = f"提取失败，启发式推断为{inferred_name}(来源:{source})"
            L(f"    字幕#{t.track_id} 提取失败 -> 启发式推断: {inferred_name}({inferred_iso})")

    # ---- 4. 生成轨道名 ----
    for t in tracks:
        if t.track_type == "audio":
            t.track_name = lang_map.make_audio_track_name(
                t.detected_iso, t.codec, t.channel_layout, t.channels,
                t.profile, t.title)
        elif t.track_type == "subtitle":
            t.track_name = lang_map.get_subtitle_display_name(
                getattr(t, 'detected_kind', None), t.detected_iso)

    # ---- 5. 策略 ----
    logger.log(f"== STAGE policy == 应用保留策略(产地={movie_info['is_domestic']})", "PIPELINE")
    policy.apply_audio_policy(tracks, config, movie_info=movie_info)
    policy.apply_subtitle_policy(tracks, config)

    # ---- 汇总计划动作 ----
    for t in tracks:
        if t.track_type in ("audio", "subtitle") and t.action == "keep":
            L(f"    保留 {t.track_type}#{t.track_id}: "
              f"lang={t.detected_iso} name='{t.track_name}'", "keep")
        elif t.track_type in ("audio", "subtitle") and t.action == "remove":
            L(f"    移除 {t.track_type}#{t.track_id}: {t.note}", "remove")
    logger.log(f"== STAGE done == 分析完成: {display_path}", "PIPELINE")
    return tracks, probe.summarize(tracks)


def process_file(path, config, log=None, orig_path=None, progress_callback=None, temp_dir=None):
    """分析并执行转封装。返回 (ok, out_path, msg, tracks)。"""
    def L(m, level="info"):
        if log:
            log(m, level)

    path, orig_path = _auto_resolve_paths(path, orig_path)
    display_path = orig_path if orig_path else path
    
    # 1. 分析文件
    L(f"分析+处理: {display_path}")
    tracks, _ = analyze_file(path, config, log, orig_path=orig_path, temp_dir=temp_dir)
    
    # 2. 执行转封装
    try:
        ok, out, msg = remux.remux(tracks, path, config, log=log,
                                    progress_callback=progress_callback,
                                    orig_path=orig_path)
    except TypeError:
        ok, out, msg = remux.remux(tracks, path, config, log=log)
        if orig_path and os.path.exists(out):
            try:
                target_dir = os.path.dirname(orig_path)
                target_out = os.path.join(target_dir, os.path.basename(out))
                log(f"    [搬运] 正在将输出文件从临时缓存移动到原目录: {target_out}")
                import shutil
                shutil.move(out, target_out)
                out = target_out
            except Exception as move_err:
                msg += f" (搬运回原目录失败: {move_err})"
                ok = False
                
    return ok, out, msg, tracks


def process_tracks(path, tracks, config, log=None, orig_path=None, progress_callback=None, temp_dir=None):
    """已有分析结果时直接转封装（跳过重新识别）。"""
    def L(m, level="info"):
        if log:
            log(m, level)

    path, orig_path = _auto_resolve_paths(path, orig_path)
    display_path = orig_path if orig_path else path
    L(f"处理(已有结果): {display_path}")
    
    try:
        ok, out, msg = remux.remux(tracks, path, config, log=log,
                                    progress_callback=progress_callback,
                                    orig_path=orig_path)
    except TypeError:
        ok, out, msg = remux.remux(tracks, path, config, log=log)
        if orig_path and os.path.exists(out):
            try:
                target_dir = os.path.dirname(orig_path)
                target_out = os.path.join(target_dir, os.path.basename(out))
                log(f"    [搬运] 正在将输出文件从临时缓存移动到原目录: {target_out}")
                import shutil
                shutil.move(out, target_out)
                out = target_out
            except Exception as move_err:
                msg += f" (搬运回原目录失败: {move_err})"
                ok = False
                
    return ok, out, msg, tracks


if __name__ == "__main__":
    import sys
    from . import config as cfg_mod
    if len(sys.argv) > 1:
        c = cfg_mod.load()
        c["audio_redetect"] = "skip"
        ok, out, msg, tracks = process_file(
            sys.argv[1], c, log=print, dry_run=False)
        print("RESULT:", ok, out, msg)
        for t in tracks:
            print(t.track_type, t.track_id, t.action, t.detected_iso,
                  t.track_name)