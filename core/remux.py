# -*- coding: utf-8 -*-
"""
转封装与标签改写：用 mkvmerge 重建 MKV。
  - 所有输出统一为 .mkv（mp4 源重新打包）。
  - 为保留轨道写入规范语言码 + mkvtoolnix 风格轨道名。
  - 按策略剔除音轨/字幕。
  - 输出到原目录，智能重命名或固定后缀。
"""
import os

from . import utils, logger


def compute_output_path(src, config, tracks=None):
    """根据配置计算输出路径。

    smart_rename=True  → 用 namer 生成规范文件名
    smart_rename=False → 旧 .fixed 后缀模式
    """
    cfg = config or {}
    suffix = cfg.get("output_suffix", ".fixed")
    if cfg.get("smart_rename", True):
        from . import namer
        movie_info = cfg.get("_tmdb_movie_info", {})
        name = namer.generate_name(src, tracks or [], config, movie_info=movie_info)
        return os.path.join(os.path.dirname(os.path.abspath(src)), name)
    # 旧后缀模式
    ext = os.path.splitext(src)[1].lower()
    if ext in (".mp4", ".m4v"):
        return os.path.splitext(src)[0] + ".mkv"
    return os.path.splitext(src)[0] + suffix + ".mkv"


def _mkvmerge(config):
    return (config or {}).get("mkvmerge_path") or "mkvmerge"


def build_command(tracks, src, out, config):
    """构造 mkvmerge 命令（列表形式）。

    改进(v14)：
      - BCP 47 繁简体字幕：cmn-hans → Language=chi, IETF=cmn-Hans
                          cmn-hant → Language=chi, IETF=cmn-Hant
      - 默认轨道标记：音轨按 cmn>yue>eng 选一条默认，字幕按双语>简中>繁中>eng 选一条默认
    """
    exe = _mkvmerge(config)
    cmd = [exe, "-o", out]

    kept_audio = []
    kept_sub = []
    for t in tracks:
        if t.action != "keep":
            continue
        if t.track_type not in ("audio", "subtitle"):
            continue
        tid = t.track_id
        iso = t.detected_iso or t.language_norm or "und"
        name = t.track_name or t.detected_name or ""

        # BCP 47 繁简体字幕码：cmn-hans → chi + cmn-Hans, cmn-hant → chi + cmn-Hant
        ietf_code = None
        mkv_lang = iso
        if iso in ("cmn", "yue"):
            ietf_code = iso
            mkv_lang = "chi"
        elif iso == "chi":
            ietf_code = "cmn"
            mkv_lang = "chi"
        elif iso == "cmn-hans":
            ietf_code = "cmn-Hans"
            mkv_lang = "chi"
        elif iso == "cmn-hant":
            ietf_code = "cmn-Hant"
            mkv_lang = "chi"

        # v22: mkvmerge v100+ --language 直接接受 IETF BCP 47 码
        # （--language-ietf 已被移除，所有情况只用 --language）
        lang_val = ietf_code if ietf_code else mkv_lang
        cmd += [f"--language", f"{tid}:{lang_val}"]
        if name:
            cmd += [f"--track-name", f"{tid}:{name}"]
        if t.track_type == "audio":
            kept_audio.append(str(tid))
        elif t.track_type == "subtitle":
            kept_sub.append(str(tid))

    # ---- 默认轨道标记 ----
    # 音轨：cmn > yue > eng > 其他（选一条默认）
    def _audio_default_priority(iso):
        iso = iso.lower()
        if iso in ("cmn", "cmn-hans", "cmn-hant", "chi"): return 0
        if iso == "yue": return 1
        if iso == "eng": return 2
        return 9
    audio_ids = [str(t.track_id) for t in tracks
                 if t.track_type == "audio" and t.action == "keep"]
    if audio_ids:
        best_audio = min(audio_ids,
                         key=lambda tid: _audio_default_priority(
                             next(t.detected_iso or "" for t in tracks
                                  if str(t.track_id) == tid)))
        for tid in audio_ids:
            flag = 1 if tid == best_audio else 0
            cmd += [f"--default-track", f"{tid}:{flag}"]

    # 字幕：cmn-Hans 双语 > cmn-Hans 简中 > cmn-Hant > eng > 其他（选一条默认）
    # 不能用简单的 iso 排序，因为双语和简中 iso 相同。用 kind 优先级。
    # 读 kind：查找 Track 的 detected_kind
    sub_priority = {"bilingual": 0, "chinese_simplified": 1,
                    "bilingual_traditional": 2, "chinese_traditional": 3,
                    "english": 4}
    sub_ids = [str(t.track_id) for t in tracks
               if t.track_type == "subtitle" and t.action == "keep"]
    # v23.54: 分离外挂字幕（external_path 有值）
    ext_subs = [t for t in tracks
                if t.track_type == "subtitle" and t.action == "keep"
                and getattr(t, "external_path", None)]
    int_sub_ids = [str(t.track_id) for t in tracks
                   if t.track_type == "subtitle" and t.action == "keep"
                   and not getattr(t, "external_path", None)]
    if int_sub_ids:
        best_sub = min(int_sub_ids,
                       key=lambda tid: sub_priority.get(
                           next(getattr(t, "detected_kind", "other") for t in tracks
                                if str(t.track_id) == tid), 99))
        for tid in int_sub_ids:
            flag = 1 if tid == best_sub else 0
            cmd += [f"--default-track", f"{tid}:{flag}"]
        cmd += ["--subtitle-tracks", ",".join(int_sub_ids)]

    # v23.54: 外挂字幕附加到主输入文件之后
    for et in ext_subs:
        ext_path = getattr(et, "external_path", None)
        if ext_path:
            cmd += ["--language", f"0:{et.detected_iso or 'und'}",
                    "--track-name", f"0:{et.detected_name or 'External'}"]
            cmd.append(ext_path)

    if kept_audio:
        cmd += ["--audio-tracks", ",".join(kept_audio)]
    else:
        cmd += ["--no-audio"]
    if kept_sub:
        cmd += ["--subtitle-tracks", ",".join(kept_sub)]
    else:
        cmd += ["--no-subtitles"]

    cmd += [src]
    return cmd


def _lang_match(expected, got_lang, got_ietf):
    """语言码匹配（v7）：
      - 中文变体统一存为 chi（Language），但保留 ietf（cmn/yue/cmn-Hans/cmn-Hant）
      - 校验时以 chi + ietf 双重匹配为准
    """
    # 归一化期望值
    exp = (expected or "und").lower()
    if exp in ("cmn", "yue"):
        exp_mkv = "chi"
        exp_ietf = exp
    elif exp == "chi":
        exp_mkv = "chi"
        exp_ietf = "cmn"
    elif exp == "cmn-hans":
        exp_mkv = "chi"
        exp_ietf = "cmn-hans"
    elif exp == "cmn-hant":
        exp_mkv = "chi"
        exp_ietf = "cmn-hant"
    else:
        exp_mkv = exp
        exp_ietf = exp
    # v22: 通用 ISO 639-1/2 归一化（如 fr ↔ fra 视为匹配）
    if exp_mkv == (got_lang or "").lower():
        return True
    if exp_ietf == (got_ietf or "").lower():
        return True
    try:
        from core import lang_map
        exp_norm = lang_map.lang_info(exp_mkv).get("iso", exp_mkv)
        got_norm = lang_map.lang_info((got_lang or "").lower()).get("iso", (got_lang or "").lower())
        if exp_norm == got_norm:
            return True
    except Exception:
        pass
    # 中文变体互匹配
    zh = {"cmn", "chi", "zho", "yue", "cmn-hans", "cmn-hant"}
    if exp_mkv in zh and (got_lang_lower in zh or got_ietf_lower in zh):
        return True
    return False


def _verify(out, tracks, config):
    """回读输出，确认保留轨道数量与语言码正确。"""
    try:
        out_tracks = utils.mkvmerge_identify(out)
    except Exception as e:
        return False, f"校验异常: {e}"

    out_by_type = {"audio": [], "subtitle": []}
    for mt in out_tracks:
        out_by_type.setdefault(mt["type"], []).append(mt)

    exp_audio = [t for t in tracks if t.track_type == "audio" and t.action == "keep"]
    exp_sub = [t for t in tracks if t.track_type == "subtitle" and t.action == "keep"]

    if len(out_by_type["audio"]) != len(exp_audio):
        return False, f"音轨数量不符: 期望{len(exp_audio)} 实际{len(out_by_type['audio'])}"
    if len(out_by_type["subtitle"]) != len(exp_sub):
        return False, f"字幕数量不符: 期望{len(exp_sub)} 实际{len(out_by_type['subtitle'])}"

    for exp, got in zip(exp_audio, out_by_type["audio"]):
        iso = (exp.detected_iso or exp.language_norm or "und")
        if not _lang_match(iso, got.get("language"), got.get("language_ietf")):
            return False, (f"音轨语言校验失败: 期望{iso} "
                           f"实际{got.get('language_ietf') or got.get('language')}")
    for exp, got in zip(exp_sub, out_by_type["subtitle"]):
        iso = (exp.detected_iso or exp.language_norm or "und")
        if not _lang_match(iso, got.get("language"), got.get("language_ietf")):
            return False, (f"字幕语言校验失败: 期望{iso} "
                           f"实际{got.get('language_ietf') or got.get('language')}")
    return True, "ok"


def remux(tracks, src, config, log=None, progress_callback=None):
    """执行转封装。返回 (ok: bool, out_path: str, msg: str)。

    输出直接写入目标路径（智能重命名或 .fixed 后缀）。
    progress_callback(pct): 可选，接收进度百分比 0-100。
    """
    def L(m, level="info"):
        if log:
            log(m, level)

    out = compute_output_path(src, config, tracks)
    # 清理 TMDB 缓存，避免影响下一个文件
    config.pop("_tmdb_movie_info", None)

    cmd = build_command(tracks, src, out, config)
    L("执行: " + " ".join(cmd))
    logger.log(f"== STAGE remux == mkvmerge 转封装 -> {out}", "PIPELINE")

    # v22: 用 Popen 逐行读取 stderr，解析进度
    import subprocess
    si = None
    if os.name == 'nt':
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    try:
        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            startupinfo=si,
        )
        stderr_lines = []
        last_pct = -1
        for line in p.stderr:
            stderr_lines.append(line)
            # 解析进度: "进度: xx%"
            if "进度:" in line and "%" in line:
                try:
                    pct_str = line.split("%")[0].rsplit()[-1]
                    pct = int(pct_str)
                    if pct != last_pct:
                        last_pct = pct
                        if progress_callback:
                            progress_callback(pct)
                except (ValueError, IndexError):
                    pass
        p.wait(timeout=1800)
        stderr_text = "".join(stderr_lines)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        p.kill()
        return False, "", "mkvmerge 超时(30分钟)"

    if rc != 0 or not os.path.exists(out):
        err_tail = stderr_text[-2000:] if stderr_text else "(无错误输出)"
        L(f"mkvmerge 失败(rc={rc})")
        return False, "", f"mkvmerge 失败(rc={rc}):\n{err_tail}"

    # v22: 输出文件验证
    ok, vmsg = _verify(out, tracks, config)
    if not ok:
        L(f"校验失败: {vmsg}")
        return False, "", f"校验失败: {vmsg}"

    L("校验通过", "ok")
    L(f"已生成: {out}")
    return True, out, "完成"
