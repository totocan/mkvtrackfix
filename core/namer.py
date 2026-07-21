# -*- coding: utf-8 -*-
"""
智能重命名引擎（v23）。

从保留轨道的 track_name 解析编码+声道（如 'French [FLAC 2.0]'），
中/英文名优先走 TMDB（通过 movie_info 传入），文件名做后备。

最终文件名：[中文名.]英文名.年份.音频编码.声道.fixed.mkv
"""

import os
import re

from . import douban

# ── 视频编解码器简化 ──────────────────────────────────
_VIDEO_CODEC_SIMPLE = {
    "v_mpegh/iso/hevc":  "H.265",
    "v_mpeg4/iso/avc":   "H.264",
    "v_mpeg4/iso/sp":    "H.264",
    "v_mpeg4/iso/asp":   "H.264",
    "v_av1":             "AV1",
    "v_vp9":             "VP9",
    "v_vp8":             "VP8",
    "v_mpeg2":           "MPEG-2",
    "v_ms/vfw/fourcc":   "VC-1",
    "v_real":            "RealVideo",
}

# ── 特殊字符映射表 ──────────────────────────────────────
_UNICODE_MAP = str.maketrans({
    "\u2022": ".",   # •
    "\u00B7": ".",   # ·
    "\u30FB": ".",   # ・
    "\u2013": "-",   # –
    "\u2014": "-",   # —
    "\u2018": "'",   # '
    "\u2019": "'",   # '
    "\u201C": "'",   # "
    "\u201D": "'",   # "
    "\u00B4": "'",   # ´
    "\uFF08": "(",   # （
    "\uFF09": ")",   # ）
    "\uFF0C": "",    # ，
    "\uFF0F": "-",   # ／
    "\uFF06": "&",   # ＆
    "\u3000": ".",   # 全角空格
    "\\": "",
    "/": "",
    ":": "",
    "*": "",
    "?": "",
    "\"": "",
    "<": "",
    ">": "",
    "|": "",
    "\u2026": "",    # …
})


def _sanitize(text):
    """清理文本：非ASCII装饰符号→ASCII、Win禁止字符删除、连续分隔符→单点。"""
    t = text.strip()
    t = t.translate(_UNICODE_MAP)
    t = re.sub(r"[. _-]+", ".", t)
    return t.strip(".")


# ── 音轨编码名简化（用于文件名，避免过长） ──
_AUDIO_CODEC_SIMPLE = {
    "dts hd master audio": "DTS-HD.MA",
    "dts-hd master audio": "DTS-HD.MA",
    "dts hd high resolution": "DTS-HiRes",
    "dts-hd high resolution": "DTS-HiRes",
    "truehd atmos": "TrueHD",
    "truehd": "TrueHD",
    "dolby digital plus": "DOLBY.DiPlus",
    "dolby digital": "AC-3",
    "e-ac-3": "E-AC3",
    "eac3": "E-AC3",
}

def _track_name_audio_info(track_name):
    """从 track_name（如 'French [FLAC 2.0]'）解析编码和声道。

    返回 (codec_label, channels_label)，解析失败返回 ("", "")。
    """
    if not track_name:
        return "", ""
    m = re.search(r"\[(.+?)\s+(\d+\.\d+)\]", track_name)
    if m:
        raw = m.group(1).strip()
        # v23.46: 简化编码名
        simplified = _AUDIO_CODEC_SIMPLE.get(raw.lower(), raw)
        return simplified, m.group(2)
    # 带单个数字声道，如 [AC-3 6ch]
    m = re.search(r"\[(.+?)\s+(\d+)ch\]", track_name)
    if m:
        return m.group(1), _channels_label(m.group(2))
    # 只有编码没有声道
    m = re.search(r"\[(.+?)\]", track_name)
    if m:
        return m.group(1), ""
    return "", ""


def _channels_label(ch):
    """声道数 → 标签（7.1 / 5.1 / 2.0）。"""
    try:
        ch = int(ch)
    except (ValueError, TypeError):
        return str(ch) if ch else ""
    if ch >= 8:    return "7.1"
    if ch >= 6:    return "5.1"
    if ch == 3:    return "2.1"
    return f"{ch}.0"


def _folder_has_chinese(src):
    """文件所在文件夹名是否包含中文。"""
    folder = os.path.basename(os.path.dirname(os.path.abspath(src)))
    return bool(re.search(r"[\u4e00-\u9fff\u3400-\u4dbf]", folder))


def generate_name(src, tracks, config, movie_info=None):
    """生成规范输出文件名。

    movie_info: 来自 douban.classify_movie 的完整返回，
                应包含 title_en / title_zh / year / country_name（由调用方填充）。

    文件名规则：[中文名.]英文名.年份.国家.分辨率.视频编码.音频编码.声道.fixed.mkv
    """
    # 1. 中/英文名 + 年份（优先 TMDB，后备文件名）
    en_name = (movie_info or {}).get("title_en", "") or ""
    cn_name = (movie_info or {}).get("title_zh", "") or ""
    year = (movie_info or {}).get("year", 0) or 0
    country_name = (movie_info or {}).get("country_name", "") or ""

    # TMDB 查不到时从文件名解析
    if not en_name:
        info = douban.extract_movie_info(src)
        en_name = (info.get("title_en") or "").strip()
        year = year or info.get("year", 0) or 0

    # 中文名仅在父文件夹无中文时加
    if cn_name and _folder_has_chinese(src):
        cn_name = ""

    # 3. 视频分辨率 + 视频编码（优先从 video track 拿，缺失时回退到源文件名）
    video_tracks = [t for t in tracks if t.track_type == "video"]
    res_label = ""
    vcodec_label = ""
    if video_tracks:
        v = video_tracks[0]
        h = getattr(v, "height", 0) or 0
        if h >= 2160:    res_label = "2160p"
        elif h >= 1080:  res_label = "1080p"
        elif h >= 720:   res_label = "720p"
        elif h >= 576:   res_label = "576p"
        elif h >= 480:   res_label = "480p"
        raw_codec = (getattr(v, "codec", "") or "").lower()
        vcodec_label = _VIDEO_CODEC_SIMPLE.get(raw_codec, "")
    # v22: 文件名兜底（某些老版本 mkvmerge 不返回 height/codec）
    fname = os.path.basename(src)
    if not res_label:
        m = re.search(r"\b(2160p|1080p|720p|576p|480p|4k|2k|1440p)\b", fname, re.I)
        if m:
            v_lower = m.group(1).lower()
            res_label = "2160p" if v_lower in ("4k",) else (
                "1440p" if v_lower == "2k" else v_lower)
    if not vcodec_label:
        m = re.search(r"\b(H\.?26[45]|HEVC|AV1|VP9|AVC|X\.?264|X\.?265|VC-?1)\b", fname, re.I)
        if m:
            c = m.group(1).upper().replace(".", "")
            vcodec_label = {"H264": "H.264", "H265": "H.265",
                            "X264": "H.264", "X265": "H.265",
                            "HEVC": "H.265", "AVC": "H.264"}.get(c, m.group(1))

    # 4. 音频编码 + 声道（从 track_name 解析，取保留音轨第一条）
    kept = [t for t in tracks
            if t.track_type == "audio" and getattr(t, "action", "keep") == "keep"]
    audio_codec, audio_ch = "", ""
    if kept:
        audio_codec, audio_ch = _track_name_audio_info(getattr(kept[0], "track_name", ""))

    # 5. 拼装文件名（仅名称部分 sanitize，编码/声道不破坏横线）
    parts = []
    if cn_name:
        parts.append(_sanitize(cn_name))
    if en_name:
        parts.append(_sanitize(en_name))
    if year:
        parts.append(str(year))
    if country_name:
        parts.append(country_name)
    if res_label:
        parts.append(res_label)
    if vcodec_label:
        parts.append(vcodec_label)
    if audio_codec:
        parts.append(audio_codec)  # 已从 track_name 解析，保留原始横线
    if audio_ch:
        parts.append(audio_ch)
    parts.append("fixed")

    name = ".".join(parts) + ".mkv"

    # 清理双点（en_name sanitize 后可能产生）
    name = name.replace("..", ".")
    # 回退保护
    if len(name) < 20 or "." not in name:
        base = os.path.splitext(os.path.basename(src))[0]
        name = _sanitize(base) + ".fixed.mkv"
        if len(name) < 10:
            name = os.path.basename(src) + ".fixed.mkv"
    if len(name) < 20 or "." not in name:
        base = os.path.splitext(os.path.basename(src))[0]
        name = _sanitize(base) + ".fixed.mkv"
        if len(name) < 10:
            name = os.path.basename(src) + ".fixed.mkv"
    return name
