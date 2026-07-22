# -*- coding: utf-8 -*-
"""
命名单一事实来源（v23.54 重构）。

职责：
  - make_audio_track_name : 生成音频轨名（写入 MKV track_name），
                            格式「语言名 [编码 声道]」，如「普通话 [DTS-HD MA 5.1]」
  - get_subtitle_track_name : 生成字幕轨名
  - generate_name          : 根据规则生成规范输出文件名
  - _track_name_audio_info : 从轨道名反解析出(编码,声道)，供 generate_name 复用

设计要点（修复此前不一致）：
  - 编码显示名 / 简写 / 声道 / Atmos 全部来自 core/codec.py，
    轨道名里的 "DTS-HD MA" / "TrueHD Atmos 7.1" 与文件名简写表同源，
    扫描模式（pipeline 写 track_name）与仅重命名模式（rename 读 track_name）
    走完全一致的映射，杜绝前后不一。
  - 修复 TrueHD Atmos 被简写成 "TrueHD" 丢失 Atmos 标记的 bug
    （codec.short_name("truehd","atmos") -> "TrueHD.Atmos"）。

文件名规则：[中文名.]英文名.年份.国家.分辨率.视频编码.音频编码.声道.fixed.mkv
"""

import os
import re

from . import codec, lang, douban


# ── 视频编解码器简化（文件名用）：ffprobe 原始 codec_id -> 简写 ──
# 与 codec.short_name 同源，这里只保留视频编码映射（音频走 codec.short_name）
_VIDEO_CODEC_SIMPLE = {
    "v_mpegh/iso/hevc": "H.265",
    "v_mpeg4/iso/avc": "H.264",
    "v_mpeg4/iso/sp": "H.264",
    "v_mpeg4/iso/asp": "H.264",
    "v_av1": "AV1",
    "v_vp9": "VP9",
    "v_vp8": "VP8",
    "v_mpeg2": "MPEG-2",
    "v_ms/vfw/fourcc": "VC-1",
    "v_real": "RealVideo",
    "h264": "H.264", "h265": "H.265", "hevc": "H.265", "av1": "AV1",
    "vp9": "VP9", "vp8": "VP8", "mpeg2video": "MPEG-2", "vc1": "VC-1",
}

# ── 特殊字符映射表（文件名清理） ──
_UNICODE_MAP = str.maketrans({
    "\u2022": ".", "\u00B7": ".", "\u30FB": ".", "\u2013": "-", "\u2014": "-",
    "\u2018": "'", "\u2019": "'", "\u201C": "'", "\u201D": "'", "\u00B4": "'",
    "\uFF08": "(", "\uFF09": ")", "\uFF0C": "", "\uFF0F": "-", "\uFF06": "&",
    "\u3000": ".", "\\": "", "/": "", ":": "", "*": "", "?": "", "\"": "",
    "<": "", ">": "", "|": "", "\u2026": "",
})


def _sanitize(text):
    """清理文本：非ASCII装饰符号→ASCII、Win禁止字符删除、连续分隔符→单点。"""
    t = text.strip()
    t = t.translate(_UNICODE_MAP)
    t = re.sub(r"[. _-]+", ".", t)
    return t.strip(".")


# ── 声道数 → 标签（7.1 / 5.1 / 2.0） ──
def _channel_label(channel_layout, channels):
    """声道标签。8声道=7.1, 6声道=5.1, 2声道=2.0, 1声道=1.0。"""
    if channel_layout:
        bl = channel_layout.split("(")[0].strip().lower()
        mapping = {
            "mono": "1.0", "stereo": "2.0", "2.1": "2.1", "3.0": "3.0",
            "3.1": "3.1", "4.0": "4.0", "quad": "4.0", "4.1": "4.1",
            "5.0": "5.0", "5.1": "5.1", "5.1(side)": "5.1", "6.0": "6.0",
            "6.1": "6.1", "7.0": "7.0", "7.1": "7.1", "7.1.2": "7.1.2",
            "7.1.4": "7.1.4", "9.1": "9.1", "10.1": "10.1",
        }
        if bl in mapping:
            return mapping[bl]
    try:
        n = int(channels)
    except (TypeError, ValueError):
        return ""
    return {1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1"}.get(n, f"{n}.0")


def make_audio_track_name(detected_iso, codec_name, channel_layout=None,
                          channels=None, profile=None, title_hint=None):
    """生成音轨名（v7 格式：语言名 [编码 声道]）。

    示例：
      - 普通话 [AAC 2.0]
      - 普通话 [TrueHD Atmos 7.1]
      - 英语 [E-AC-3 5.1]
      - 粤语 [DTS-HD MA 5.1]
      - French [AC-3 5.1]

    编码显示名与 Atmos 处理统一走 codec.display_name / codec._dts_subtype，
    与文件名简写同源（见 _track_name_audio_info）。
    """
    # 语言名（混合表达，来自 lang）
    lang_name = lang.get_track_display_name(detected_iso, track_type="audio")
    if not lang_name or lang_name == detected_iso:
        lang_name = "未知"

    # 编码显示名（来自 codec 单一来源，含 DTS 子类型）
    disp = codec.display_name(codec_name, profile)

    # Atmos（profile 或 title 提示）
    atmos = ("atmos" in (profile or "").lower()) or \
            ("atmos" in (title_hint or "").lower())

    ch = _channel_label(channel_layout, channels)

    tech = disp
    if atmos:
        tech = f"{disp} Atmos"
    if ch:
        tech = f"{tech} {ch}".strip()

    if tech:
        return f"{lang_name} [{tech}]"
    return lang_name


def get_subtitle_track_name(kind, iso):
    """生成字幕轨名（混合表达），直接转发 lang.get_subtitle_display_name。"""
    return lang.get_subtitle_display_name(kind, iso)


def _track_name_audio_info(track_name):
    """从 track_name（如 'French [FLAC 2.0]'）解析编码和声道。

    返回 (codec_short_label, channels_label)，解析失败返回 ("", "")。

    修复点：编码简写通过 codec.short_name 统一解析，
    此前 namer 用 _AUDIO_CODEC_SIMPLE（键为全拼小写）匹配 track_name 里的
    "DTS-HD MA" 永远失败 → 现改为对括号内的编码名做归一化查 codec.short_name。
    """
    if not track_name:
        return "", ""
    # 优先解析 "编码 声道" 形式（如 [FLAC 2.0] / [DTS-HD MA 5.1] / [TrueHD Atmos 7.1.2]）
    # 声道部分贪婪匹配多段小数（7.1.2）或 "Nch"，避免把 Atmos 之后的小数截断
    m = re.search(r"\[(.+?)\s+((?:\d+\.)*\d+ch|\d+(?:\.\d+)+)\]", track_name)
    if m:
        raw = m.group(1).strip()
        simplified = codec.short_name(raw)
        ch_raw = m.group(2)
        # "Nch" 形式（如 6ch）转为 5.1；多段小数（如 7.1.2）原样保留
        if ch_raw.endswith("ch"):
            ch = _channels_label(ch_raw)
        else:
            ch = ch_raw
        return simplified, ch
    # 带单个数字声道，如 [AC-3 6ch]
    m = re.search(r"\[(.+?)\s+(\d+)ch\]", track_name)
    if m:
        return codec.short_name(m.group(1)), _channels_label(m.group(2))
    # 只有编码没有声道
    m = re.search(r"\[(.+?)\]", track_name)
    if m:
        return codec.short_name(m.group(1)), ""
    return "", ""


def _channels_label(ch):
    """声道数 → 标签（7.1 / 5.1 / 2.0）。支持 "6ch" 这类带单位写法。"""
    raw = str(ch or "").strip().lower().replace("ch", "").strip()
    try:
        n = int(raw)
    except (ValueError, TypeError):
        return str(ch) if ch else ""
    if n >= 8:
        return "7.1"
    if n >= 6:
        return "5.1"
    if n == 3:
        return "2.1"
    return f"{n}.0"


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
        w = getattr(v, "width", 0) or 0
        h = getattr(v, "height", 0) or 0
        # 统一走 codec.resolution_label（标准档位 + 容差对齐，宽度优先）
        # 解决 1920x1038 这类被硬阈值误降为 720p 的问题
        res_label = codec.resolution_label(w, h)
        raw_codec = (getattr(v, "codec", "") or "").lower()
        vcodec_label = _VIDEO_CODEC_SIMPLE.get(raw_codec, "")
    # 文件名兜底（某些老版本 mkvmerge 不返回 width/height/codec）
    fname = os.path.basename(src)
    if not res_label:
        m = re.search(r"\b(2160p|1440p|1080p|720p|576p|480p|4k|2k)\b", fname, re.I)
        if m:
            res_label = codec.normalize_resolution_text(m.group(1))
    if not vcodec_label:
        m = re.search(r"\b(H\.?26[45]|HEVC|AV1|VP9|AVC|X\.?264|X\.?265|VC-?1)\b",
                      fname, re.I)
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
        parts.append(audio_codec)  # 已从 track_name 经 codec.short_name 解析
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


if __name__ == "__main__":
    # 自检：轨道名 → 文件名简写 的互逆一致性
    # 写轨道名（扫描模式）
    tn1 = make_audio_track_name("cmn", "dts", "5.1", 6, "DTS-HD MA")
    tn2 = make_audio_track_name("cmn", "truehd", "7.1.2", 10, "atmos")
    tn3 = make_audio_track_name("eng", "eac3", "5.1(side)", 6)
    print("轨道名:", tn1, "|", tn2, "|", tn3)
    assert "DTS-HD MA" in tn1
    assert "TrueHD Atmos 7.1" in tn2, tn2   # Atmos 不再被吞掉
    # 反解析（仅重命名模式）
    c1, ch1 = _track_name_audio_info(tn1)
    c2, ch2 = _track_name_audio_info(tn2)
    c3, ch3 = _track_name_audio_info(tn3)
    print("反解析:", c1, ch1, "|", c2, ch2, "|", c3, ch3)
    assert c1 == "DTS-HD.MA", c1
    assert c2 == "TrueHD.Atmos", c2          # 文件名简写保留 Atmos
    assert ch2 == "7.1", ch2
    assert c3 == "EAC3", c3
    print("naming.py 自检通过")
