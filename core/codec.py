# -*- coding: utf-8 -*-
"""
编码单一事实来源（v23.54 重构核心）。

此前编码名分散在多处、且键格式不一致，导致：
  - lang_map._CODEC_DISPLAY 用于「写入 MKV 的轨道名」（人类可读：DTS-HD MA / TrueHD Atmos 7.1）
  - namer._AUDIO_CODEC_SIMPLE / _VIDEO_CODEC_SIMPLE 用于「文件名简写」（短：DTS-HD.MA）
    且音频简写表的键是 "dts hd master audio" 全拼小写，而 track_name 里实际是 "DTS-HD MA"
    → 永远匹配不上，简写形同虚设，文件名里音频编码经常为空。

本模块统一三张表，供 lang_map / namer / policy 共用，杜绝前后不一致：

  - _CODEC_BASE_DISPLAY : ffprobe 原始 codec 名 -> 人类可读显示名
  - _CODEC_SHORT        : 人类可读显示名 / 原始 codec 名 -> 文件名简写
  - _CODEC_WEIGHT       : 原始 codec 名 -> 质量权重（同语言多轨择优用）
  - _dts_subtype()      : 根据 profile 推断 DTS 子类型显示名
  - display_name()      : 显示名（供 make_audio_track_name）
  - short_name()        : 文件名简写（供 namer 反解析 / 生成）
  - quality_weight()    : 质量权重
  - is_video_codec()    : 判断是否视频编码
"""

# ---------------------------------------------------------------------------
# 1). 基础显示名：ffprobe 原始 codec 字符串 -> 人类可读
#     key 统一用小写原始 codec_id（与 ffprobe 返回一致）
# ---------------------------------------------------------------------------
_CODEC_BASE_DISPLAY = {
    "aac": "AAC", "ac3": "AC-3", "eac3": "E-AC-3", "dts": "DTS",
    "truehd": "TrueHD", "flac": "FLAC", "alac": "ALAC", "opus": "Opus",
    "mp3": "MP3", "mp2": "MP2",
    "pcm_s16le": "PCM", "pcm_s24le": "PCM", "pcm_s32le": "PCM",
    "pcm_f32le": "PCM", "pcm_f64le": "PCM",
    "vorbis": "Vorbis", "mlp": "MLP", "tta": "TTA",
    "wavpack": "WavPack", "speex": "Speex", "cook": "Cook",
    "real": "RealAudio", "dts-hd": "DTS-HD", "dtshd": "DTS-HD",
    # 视频
    "h264": "H.264", "h265": "H.265", "hevc": "H.265", "av1": "AV1",
    "vp9": "VP9", "vp8": "VP8", "mpeg2video": "MPEG-2",
    "vc1": "VC-1", "mpeg4": "MPEG-4", "x264": "H.264", "x265": "H.265",
}

# ---------------------------------------------------------------------------
# 2). 文件名简写：人类可读显示名 -> 短标签
#     key 同时覆盖「显示名」与「原始 codec 名」，大小写不敏感匹配
#     这正是修复点：track_name 里是 "DTS-HD MA" / "TrueHD Atmos 7.1"，
#     以前简写表的 key 是 "dts hd master audio" 全拼，匹配不上。
# ---------------------------------------------------------------------------
_CODEC_SHORT = {
    # 音频（显示名形式，与 make_audio_track_name 输出一致）
    "aac": "AAC", "ac3": "AC3", "ac-3": "AC3", "eac3": "EAC3",
    "e-ac-3": "EAC3", "dts": "DTS", "dts-hd ma": "DTS-HD.MA",
    "dts-hd hra": "DTS-HD.HRA", "dts-hd": "DTS-HD",
    "dts-hd ma 7.1": "DTS-HD.MA", "dts-hd ma 5.1": "DTS-HD.MA",
    "truehd": "TrueHD", "truehd atmos": "TrueHD.Atmos",
    "truehd atmos 7.1": "TrueHD.Atmos", "flac": "FLAC", "alac": "ALAC",
    "opus": "Opus", "mp3": "MP3", "mp2": "MP2", "pcm": "PCM",
    "vorbis": "Vorbis", "mlp": "MLP",
    # 视频（原始 codec_id 形式 + 显示名形式）
    "h264": "H.264", "h.264": "H.264", "x264": "H.264",
    "h265": "H.265", "h.265": "H.265", "hevc": "H.265", "x265": "H.265",
    "av1": "AV1", "vp9": "VP9", "vp8": "VP8", "mpeg2video": "MPEG2",
    "mpeg-2": "MPEG2", "vc1": "VC-1", "vc-1": "VC-1", "mpeg4": "MPEG4",
}

# ---------------------------------------------------------------------------
# 3). 质量权重：原始 codec_id -> 权重（同语言多音轨排序用）
#     TrueHD Atmos = 10, TrueHD = 9, FLAC/ALAC = 8, DTS-HD MA = 7 ...
# ---------------------------------------------------------------------------
_CODEC_WEIGHT = {
    "aac": 3, "ac3": 5, "eac3": 5, "dts": 5, "truehd": 9, "flac": 8,
    "alac": 7, "opus": 3, "mp3": 2, "mp2": 2, "pcm_s16le": 6,
    "pcm_s24le": 6, "pcm_s32le": 6, "pcm_f32le": 6, "pcm_f64le": 6,
    "vorbis": 2, "mlp": 9, "tta": 4, "wavpack": 4, "speex": 2,
    "cook": 2, "real": 2, "dts-hd": 7, "dtshd": 7,
}

# DTS profile 关键字 -> 子类型显示名
_DTS_SUBTYPE = {
    "hdma": "DTS-HD MA", "ma": "DTS-HD MA", "xll": "DTS-HD MA",
    "hra": "DTS-HD HRA", "hd": "DTS-HD HRA", "es": "DTS-ES",
    "express": "DTS 96/24",
}

# 视频编码原始 codec_id 集合（用于 is_video_codec 判断）
_VIDEO_CODECS = {
    "h264", "h265", "hevc", "av1", "vp9", "vp8", "mpeg2video",
    "vc1", "mpeg4", "x264", "x265", "v_mpegh/iso/hevc",
    "v_mpeg4/iso/avc", "v_mpeg4/iso/sp", "v_mpeg4/iso/asp",
    "v_av1", "v_vp9", "v_vp8", "v_mpeg2", "v_ms/vfw/fourcc", "v_real",
}


def _norm_key(s):
    """简写表查找键归一化：小写、去多余空格、保留必要连字符。"""
    return (s or "").strip().lower()


def _dts_subtype(profile):
    """根据 ffprobe profile 推断 DTS 子类型显示名；非 DTS 或无法识别返回 None。"""
    prof = _norm_key(profile)
    if not prof:
        return None
    # 优先精确匹配子类型关键字
    if "hdma" in prof or "ma" in prof or "xll" in prof:
        return "DTS-HD MA"
    if "hra" in prof or "hd" in prof:
        return "DTS-HD HRA"
    if "es" in prof:
        return "DTS-ES"
    if "express" in prof:
        return "DTS 96/24"
    return None


def display_name(codec_name, profile=None):
    """编码显示名（人类可读，用于写入 MKV 轨道名）。

    - 基础名查 _CODEC_BASE_DISPLAY
    - DTS 类按 profile 升级子类型（DTS-HD MA / DTS-HD HRA）
    - 查不到则原样大写返回
    """
    c = _norm_key(codec_name)
    disp = _CODEC_BASE_DISPLAY.get(c)
    if disp is None:
        disp = (codec_name or "").upper() if codec_name else ""
    # DTS 子类型升级
    if c == "dts":
        sub = _dts_subtype(profile)
        if sub:
            disp = sub
    return disp


def short_name(codec_name, profile=None):
    """文件名简写（短标签，供 namer 反解析 / 生成）。

    查找顺序：
      1. 先用 display_name 得到人类可读名，再查 _CODEC_SHORT（覆盖 "DTS-HD MA" 等）
      2. 直接用原始 codec 名查 _CODEC_SHORT
      3. 都查不到 -> 用 display_name 原样（保证至少有可读名）
    """
    c = _norm_key(codec_name)
    prof = _norm_key(profile)
    atmos = ("atmos" in prof) or ("atmos" in c)
    disp = display_name(codec_name, profile)
    # 显示名形式（如 "DTS-HD MA"、"TrueHD Atmos"）
    key_disp = _norm_key(disp)
    # Atmos 组合键：把 Atmos 拼到显示名后，匹配 _CODEC_SHORT 里的
    # "truehd atmos" / "dts-hd ma 7.1" 等条目
    if atmos:
        combo = f"{key_disp} atmos"
        if combo in _CODEC_SHORT:
            return _CODEC_SHORT[combo]
    if key_disp in _CODEC_SHORT:
        return _CODEC_SHORT[key_disp]
    # 原始 codec_id 形式
    if c in _CODEC_SHORT:
        return _CODEC_SHORT[c]
    # 兜底：Atmos 但无对应简写条目时给出合理默认
    if atmos:
        return "TrueHD.Atmos" if "truehd" in key_disp else f"{disp}.Atmos"
    return disp  # 兜底：原样返回显示名（不丢信息）


def quality_weight(codec_name, profile=None):
    """编码质量权重（同语言多音轨择优）。Atmos 额外 +1 封顶 10。"""
    c = _norm_key(codec_name)
    w = _CODEC_WEIGHT.get(c, 2)
    prof = _norm_key(profile)
    # DTS 子类型提升
    if c == "dts":
        if "atmos" in prof or "hdma" in prof or "ma" in prof or "xll" in prof:
            w = 7
        elif "hra" in prof or "hd" in prof:
            w = 6
    # Atmos 额外提升
    if "atmos" in prof or "atmos" in _norm_key(codec_name):
        w = max(w, 10)
    return w


def is_video_codec(codec_name):
    """是否为视频编码原始 codec_id。"""
    return _norm_key(codec_name) in _VIDEO_CODECS


# ---------------------------------------------------------------------------
# 分辨率判定（v23.54 重构：统一为单一来源）
#   此前 naming.generate_name 用硬阈值 height>=1080 判 1080p，
#   导致 1920x1038 / 1920x1040 这类「变形/裁切高度」被降级成 720p。
#   实际 1038 远高於 720p、且宽度 1920 明确是 1080p 内容，应归 1080p。
#
#   规则：以「标准档位 + 容差对齐」替代硬阈值，宽度优先（1080p 内容宽度几乎都是 1920）。
#     标准档位（宽 x 高）：
#       480p  = 854  x 480
#       576p  = 1024 x 576
#       720p  = 1280 x 720
#       1080p = 1920 x 1080
#       1440p = 2560 x 1440
#       2160p = 3840 x 2160
#     容差：实际边（宽或高）落在标准值的 [0.9, 1.1] 倍内即对该档；
#           取 宽、高 各自对齐后较高的档位，避免 1038 被误降。
# ---------------------------------------------------------------------------
_RES_STANDARDS = [
    # (label, std_width, std_height)
    ("2160p", 3840, 2160),
    ("1440p", 2560, 1440),
    ("1080p", 1920, 1080),
    ("720p", 1280, 720),
    ("576p", 1024, 576),
    ("480p", 854, 480),
]
_RES_TOLERANCE = 0.10  # ±10% 容差


def _snap_to_standard(value, std):
    """value 是否落在 std 的容差区间内（含 ±10%）。"""
    if not value or not std:
        return False
    return (std * (1 - _RES_TOLERANCE)) <= value <= (std * (1 + _RES_TOLERANCE))


# 最高档（4K）宽度一旦命中即认该档，不走高度断崖降级
# （3840x1080 等极端超宽属 4K 内容，用户认可为 2160p）
_TOP_RES_LABEL = "2160p"


def resolution_label(width=None, height=None):
    """由视频宽高推断分辨率标签（480p/576p/720p/1080p/1440p/2160p）。

    采用「标准档位 + 容差对齐，宽度优先、短板约束」：

      1. 先用 **宽度** 对齐到候选档 Rw（1080p 内容宽度几乎都是 1920）；
      2. 校验 **高度** 是否达到 Rw 的「有效下限」（Rw 标准高 × 0.7）：
         - 高度 ≥ 下限（如 1080p 下限 = 1080×0.7 = 756）：视为同档裁切/变形，
           取 Rw（解决 1920x1038 / 1920x800 被误降为 720p）；
         - 高度明显不足（如 1920x720 的 720 < 756，属扁平宽屏 2.39:1）：
           改用高度重新对齐到更低档 Rh，避免误标 1080p；
         - **特例（4K）**：若 Rw 已是最最高档 2160p（宽度 3840 明确命中），
           无论高度多低（如 3840x1080 极端超宽）都认 2160p，
           因为 3840 宽度本身就是 4K 的明确信号，不应被高度拖垮；
      3. 宽高任一缺失则尽量用另一维度；都不在容差内则用「最近邻」兜底。

    - 容差 ±10% 用于精确对齐（避免 1038/1040 这类被硬阈值误降）；
    - 0.7 下限用于区分「轻微裁切」与「扁平宽屏断崖」（仅作用于非最高档）。
    """
    try:
        w = int(width or 0)
    except (TypeError, ValueError):
        w = 0
    try:
        h = int(height or 0)
    except (TypeError, ValueError):
        h = 0
    if not w and not h:
        return ""

    # 1) 宽度优先对齐候选档 Rw
    rw_label, rw_std = "", 0
    for label, sw, sh in _RES_STANDARDS:
        if _snap_to_standard(w, sw):
            rw_label, rw_std = label, sh
            break

    # 2) 宽度命中：按高度有效下限约束
    if rw_label:
        # 特例：最高档（4K）宽度命中即认 2160p，不受高度断崖影响
        if rw_label == _TOP_RES_LABEL:
            return rw_label
        if not h or h >= rw_std * 0.7:
            return rw_label  # 高度达标或缺失 → 取 Rw
        # 高度断崖（扁平宽屏）→ 改用高度对齐
        for label, sw, sh in _RES_STANDARDS:
            if _snap_to_standard(h, sh):
                return label
        # 高度也不在容差内：最近邻兜底（取最接近的标准档）
        return _nearest_standard(h)

    # 3) 宽度未命中：改用高度对齐
    for label, sw, sh in _RES_STANDARDS:
        if _snap_to_standard(h, sh):
            return label
    # 4) 都未精确命中：最近邻兜底（优先高度方向）
    if h:
        return _nearest_standard(h)
    if w:
        return _nearest_standard(w, use_width=True)
    return ""


def _nearest_standard(value, use_width=False):
    """value 不在容差内时，取偏离最小的标准档（按比例距离）。"""
    if not value:
        return ""
    best, best_dist = "", 1e9
    for label, sw, sh in _RES_STANDARDS:
        std = sw if use_width else sh
        if not std:
            continue
        dist = abs(value - std) / std
        if dist < best_dist:
            best_dist, best = dist, label
    return best


def normalize_resolution_text(text):
    """把文件名里的分辨率写法（4k/2k/1080p...）归一化为标准标签。

    供 naming 文件名兜底使用，与 resolution_label 共用一套标准。
    """
    t = (text or "").strip().lower()
    mapping = {
        "4k": "2160p", "2160p": "2160p", "uhd": "2160p",
        "2k": "1440p", "1440p": "1440p",
        "1080p": "1080p", "1080i": "1080p",
        "720p": "720p", "720i": "720p",
        "576p": "576p", "576i": "576p",
        "480p": "480p", "480i": "480p",
    }
    return mapping.get(t, t)


if __name__ == "__main__":
    # 自检：验证核心映射正确、两端一致
    assert display_name("dts", "DTS-HD MA") == "DTS-HD MA"
    assert display_name("truehd", "atmos") == "TrueHD"
    assert short_name("truehd", "atmos") == "TrueHD.Atmos", short_name("truehd", "atmos")
    assert short_name("dts", "DTS-HD MA") == "DTS-HD.MA", short_name("dts", "DTS-HD MA")
    assert short_name("eac3") == "EAC3"
    assert short_name("ac3") == "AC3"
    assert short_name("flac") == "FLAC"
    assert short_name("h265") == "H.265"
    assert short_name("hevc") == "H.265"
    assert quality_weight("truehd", "atmos") == 10
    assert quality_weight("flac") == 8
    assert is_video_codec("hevc") is True
    assert is_video_codec("aac") is False
    # 分辨率判定（核心修复：1920x1038 不再误降为 720p）
    assert resolution_label(1920, 1038) == "1080p", resolution_label(1920, 1038)
    assert resolution_label(1920, 1080) == "1080p"
    assert resolution_label(1920, 720) == "720p"   # 扁平宽屏断崖 → 720p
    assert resolution_label(1920, 800) == "1080p"  # 宽度优先
    assert resolution_label(1280, 720) == "720p"
    assert resolution_label(720, 404) == "480p"    # WebRip 最近邻兜底
    assert resolution_label(3840, 2160) == "2160p"  # 标准 4K
    assert resolution_label(3840, 1600) == "2160p"  # 4K 带黑边电影 2.4:1
    assert resolution_label(3840, 2076) == "2160p"  # 4K 轻微裁切 2.0:1
    assert resolution_label(4096, 2160) == "2160p"  # DCI 4K（最近邻兜底）
    assert resolution_label(4096, 1744) == "2160p"  # DCI 4K 带黑边
    assert resolution_label(3840, 1080) == "2160p"  # 4K 极端超宽，用户认可为 2160p
    assert resolution_label(4096, 1080) == "2160p"   # DCI 4K 极端超宽
    print("codec.py 自检通过")
