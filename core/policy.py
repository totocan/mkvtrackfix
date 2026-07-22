# -*- coding: utf-8 -*-
"""
保留 / 移除策略（v7 重构）。

音轨规则：
  1. 语言优先级：普通话(cmn) > 粤语(yue) > 英语(eng) > 其他
  2. 国产电影（豆瓣/启发式判定）→ 只保留普通话/粤语，去掉英语
  3. 外国电影 → 保留英语 + 普通话，去掉其他
  4. 同语言多音轨 → 只保留音质/声道最好的一条
  5. 兜底：所有音轨都不在优先列表时，保留声道最好的一条
  6. 至少保留一条音轨

字幕规则：
  1. 简中英双语 → 保留（最完整，已含简体中文与英文）
  2. 独立简体中文 → 仅当没有简中英双语时保留；有双语则视为冗余移除
     （可通过 sub_remove_redundant_simplified_if_bilingual 关闭）
  3. 纯英文 → 仅当没有简中也没有双语时保留；否则移除（双语已含英文）
  4. 繁体中文 / 其他语言 → 移除
  5. 未知类型 → 保守保留
"""
import re


def _channels(track):
    """获取音轨声道数。"""
    try:
        return int(track.channels or 0)
    except Exception:
        return 0


def _audio_quality_score(track):
    """音轨质量评分：声道数 * 编码质量权重。

    编码质量权重（粗略，用于同语言多轨排序）：
      TrueHD Atmos = 10, TrueHD = 9, FLAC = 8, DTS-HD MA = 7,
      AC-3/E-AC-3/DDP = 5, AAC = 3, 其他 = 2
    """
    ch = _channels(track)
    codec = (track.codec or "").lower()
    profile = (track.profile or "").lower()
    title_hint = (track.title or "").lower()

    weights = {
        "truehd": 9, "flac": 8, "alac": 7,
        "dts": 5,  # 基础 DTS，可能被 profile 提升
        "ac3": 5, "eac3": 5, "aac": 3,
        "opus": 3, "mp3": 2, "mp2": 2,
        "pcm_s16le": 6, "pcm_s24le": 6, "pcm_s32le": 6, "pcm_f32le": 6,
        "vorbis": 2, "wavpack": 4,
    }

    w = weights.get(codec, 2)

    # DTS 子类型提升
    if codec == "dts":
        if "atmos" in profile or "atmos" in title_hint:
            w = 10
        elif "hdma" in profile or "ma" in profile or "xll" in profile:
            w = 7
        elif "hra" in profile or "hd" in profile:
            w = 6

    # Atmos 提升
    if "atmos" in profile or "atmos" in title_hint:
        w = max(w, 10)

    return ch * w


# ---------------------------------------------------------------------------
# 音轨策略
# ---------------------------------------------------------------------------
def apply_audio_policy(tracks, config, movie_info=None):
    """对音轨设置 action / note。

    改进(v7)：
    - 引入 movie_info（产地/原生语言判断结果）
    - 国产电影：只保留 cmn/yue，去掉 eng
    - 外国电影：保留 eng + cmn，去掉其他
    - 同语言只保留最佳（质量评分最高）
    - 兜底：保留质量最好的一条
    - 至少保留一条
    """
    cfg = config or {}
    reduce = cfg.get("audio_reduce", True)
    keep_best_only = cfg.get("audio_keep_best_only", True)
    domestic_drop_eng = cfg.get("domestic_drop_english", True)

    audios = [t for t in tracks if t.track_type == "audio"]
    for t in audios:
        t.action = "keep"

    if not audios or not reduce:
        return

    # ---- 产地判断 ----
    is_domestic = False
    native_lang = "und"
    if movie_info:
        is_domestic = movie_info.get("is_domestic", False)
        native_lang = movie_info.get("native_lang", "und")

    # ---- 确定保留语言集合 ----
    isos = {t.detected_iso for t in audios if t.detected_iso}
    has_cmn = "cmn" in isos or "chi" in isos or "zho" in isos
    has_yue = "yue" in isos
    has_eng = "eng" in isos
    has_zh = has_cmn or has_yue  # 有任何中文变体

    if is_domestic and domestic_drop_eng:
        # 国产电影：只保留普通话/粤语
        keep_set = set()
        if has_cmn:
            keep_set.add("cmn")
        if has_yue:
            keep_set.add("yue")
        # 兜底：如果既没有cmn也没有yue但有chi/zho
        if not keep_set:
            for iso in ("chi", "zho"):
                if iso in isos:
                    keep_set.add(iso)
        # 国产但完全没有中文音轨 → 保留所有音轨（异常情况）
        if not keep_set:
            keep_set = isos
    else:
        # 外国电影：保留英语 + 普通话（粤语作为普通话的降级）
        keep_set = set()
        if has_eng:
            keep_set.add("eng")
        if has_cmn:
            keep_set.add("cmn")
        elif has_yue:
            keep_set.add("yue")  # 无普通话时粤语降级
        # 如果优先语言都没有 → 保留所有（异常情况兜底）
        if not keep_set:
            keep_set = isos

    # ---- 标记保留/移除 ----
    # 归一化：chi/zho 视为 cmn
    for t in audios:
        iso = t.detected_iso
        if iso in ("chi", "zho"):
            iso = "cmn"  # 归一化
        if iso in keep_set:
            t.action = "keep"
        else:
            t.action = "remove"
            t.note = f"非目标语言({t.detected_name or t.detected_iso})，按策略移除"

    # ---- 同语言多轨：只保留最佳（质量评分最高） ----
    if keep_best_only:
        best = {}
        for t in audios:
            if t.action != "keep":
                continue
            key = t.detected_iso
            if key in ("chi", "zho"):
                key = "cmn"  # 归一化
            score = _audio_quality_score(t)
            if key not in best or score > _audio_quality_score(best[key]):
                best[key] = t
        for t in audios:
            key = t.detected_iso
            if key in ("chi", "zho"):
                key = "cmn"
            if t.action == "keep" and best.get(key) is not t:
                t.action = "remove"
                t.note = "同语言多音轨，仅保留音质最佳一条"

    # ---- 兜底：至少保留一条音轨 ----
    kept = [t for t in audios if t.action == "keep"]
    if not kept and audios:
        # 选质量最好的一条
        best_all = max(audios, key=_audio_quality_score)
        best_all.action = "keep"
        best_all.note = "兜底：无优先语言音轨，保留音质最好的一条"
        for t in audios:
            if t is not best_all:
                t.action = "remove"
                t.note = "兜底：无优先语言音轨，仅保留音质最好的一条"


# ---------------------------------------------------------------------------
# 字幕策略
# ---------------------------------------------------------------------------
# 字幕类型优先级排序（数值越小优先级越高）
_SUB_KIND_PRIORITY = {
    "chinese_simplified": 1,     # 简体中文 → 最高优先
    "bilingual": 2,              # 简中英双语 → 第二优先
    "english": 3,                # 纯英文 → 第三优先（有中文/双语时去掉）
    "chinese_traditional": 100,  # 繁体中文 → 去掉
    "bilingual_traditional": 101,  # 繁中英双语 → 去掉
    "other": 102,                # 其他语言 → 去掉
    "unknown": 103,              # 未知 → 去掉
}


def apply_subtitle_policy(tracks, config):
    """对字幕设置 action / note。

    规则（与用户需求对齐）：
      1. 简中英双语 → 保留（最完整，已含简体中文与英文）
      2. 独立简体中文 → 仅当没有双语时保留；有双语则视为冗余移除
         （开关 sub_remove_redundant_simplified_if_bilingual，默认开）
      3. 纯英文 → 仅当没有简中也没有双语时保留；否则移除（双语已含英文）
      4. 繁体中文 / 其他语言 → 移除
      5. 未知类型 → 保守保留
    """
    cfg = config or {}
    remove_trad = cfg.get("sub_remove_traditional", True)
    remove_eng_if_bilingual = cfg.get("sub_remove_pure_english_if_bilingual", True)
    remove_redundant_simplified = cfg.get(
        "sub_remove_redundant_simplified_if_bilingual", True)

    subs = [t for t in tracks if t.track_type == "subtitle"]

    # 统计已有的字幕类型
    has_simplified = False
    has_bilingual = False
    has_any_chinese = False

    for t in subs:
        kind = getattr(t, "detected_kind", None) or "unknown"
        if kind == "chinese_simplified":
            has_simplified = True
            has_any_chinese = True
        elif kind == "bilingual":
            has_bilingual = True
            has_any_chinese = True
        elif kind in ("chinese_traditional", "bilingual_traditional"):
            has_any_chinese = True

    for t in subs:
        kind = getattr(t, "detected_kind", None) or "unknown"

        # v23.54: 外挂字幕（external_path 有值）默认保留，不参与内置字幕去重/移除策略
        if getattr(t, "external_path", None):
            t.action = "keep"
            t.note = "外挂字幕，默认保留"
            continue

        if kind == "chinese_simplified":
            # 已有简中英双语时，独立简体中文属于冗余，按策略移除
            if remove_redundant_simplified and has_bilingual:
                t.action = "remove"
                t.note = "已有简中英双语字幕，独立简体中文冗余，按策略移除"
            else:
                t.action = "keep"
                t.note = ""
        elif kind == "bilingual":
            t.action = "keep"
            t.note = ""
        elif kind == "chinese_traditional":
            if remove_trad:
                t.action = "remove"
                t.note = "繁体中文字幕，按策略移除"
            else:
                t.action = "keep"
                t.note = ""
        elif kind == "english":
            # 有简中或双语时去掉纯英文（双语已含英文）
            if remove_eng_if_bilingual and (has_simplified or has_bilingual):
                t.action = "remove"
                t.note = "已有简中/简中英双语字幕，纯英文可去掉"
            elif not has_any_chinese:
                # 无任何中文字幕 → 保留英文
                t.action = "keep"
                t.note = "无中文字幕，保留纯英文字幕"
            else:
                t.action = "keep"
                t.note = ""
        elif kind == "unknown":
            # 未知类型 → 保守保留（避免误删用户可能需要的字幕）
            t.action = "keep"
            t.note = "字幕类型未知，保守保留"
        else:
            # 其他语言 → 去掉（除非是优先语言列表中的）
            t.action = "remove"
            t.note = f"非目标字幕({t.detected_name or kind})，按策略移除"

    # v22: 纯英文字幕——多字幕只保留一条，优先文本(srt/ass)
    def _is_image(codec):
        c = (codec or "").lower()
        return any(k in c for k in ("pgs", "hdmv", "vobsub", "dvd", "sup", "idx"))
    eng_all = [t for t in subs if getattr(t, "action", "keep") != "remove"
               and getattr(t, "detected_kind", None) == "english"]
    if len(eng_all) > 1:
        # 按文本→图像排序，文本中的第一个保留
        eng_sorted = sorted(eng_all, key=lambda t: _is_image(t.codec))
        for t in eng_sorted[1:]:
            t.action = "remove"
            t.note = "多条英文字幕，仅保留最佳一条"
