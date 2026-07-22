# -*- coding: utf-8 -*-
"""
语言码注册表 / 简繁判定 / 轨道名生成 / und 启发式推断 —— 兼容层（v23.54）。

v23.54 起语言系统已抽到 core/lang.py（纯语言表、简繁、分类、推断）；
音轨/字幕轨道名生成已抽到 core/naming.py（依赖 lang + codec 单一来源）。

本文件保留为**向后兼容转发层**，旧代码 `from . import lang_map` 仍可工作。
新增代码请直接 `from . import lang` / `from . import naming`。
"""

# 语言系统（表、简繁、分类、推断、coerce 等）全部来自 lang
from .lang import *                       # noqa: F401,F403
from .lang import (                       # noqa: F401 显式列出关键符号，便于 IDE/静态检查
    LANG_TABLE, ANG_TABLE, _ISO_TO_LANG, _TRAD2SIMP, _SIMP_CHARS, _TRAD_CHARS,
    _CJK_RE, _LATIN_RE, _count_cjk, _count_latin, _max_consecutive_eng_words,
    is_traditional, count_trad_chars, lang_info, lang_info_by_iso,
    get_track_display_name, classify_subtitle_text, get_subtitle_display_name,
    heuristic_infer_language, coerce_lang_code, is_chinese_code,
    normalize_iso_to_cmn, _unknown,
)

# 轨道名生成（v23.54 迁移到 naming，但旧调用 lang_map.make_audio_track_name 仍可用）
from .naming import (                     # noqa: F401
    make_audio_track_name,
    get_subtitle_track_name as get_subtitle_track_name,
)

# 兼容：旧代码引用的别名（lang_map.get_subtitle_display_name 已在上面 * 导入）
__all__ = list(sorted(set(
    [n for n in dir() if not n.startswith("__")]
)))
