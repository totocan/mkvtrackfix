# -*- coding: utf-8 -*-
"""
命名模块兼容层（v23.54）。

v23.54 起命名逻辑统一在 core/naming.py（依赖 codec + lang 单一来源）。
本文件仅作向后兼容转发，旧代码 `from . import namer` / `namer.generate_name`
仍可正常工作。新增代码请直接 `from . import naming`。
"""

from .naming import (                  # noqa: F401
    generate_name, make_audio_track_name, get_subtitle_track_name,
    _track_name_audio_info, _sanitize, _channel_label, _channels_label,
    _folder_has_chinese, _VIDEO_CODEC_SIMPLE,
)
