# -*- coding: utf-8 -*-
"""
媒体轨道解析：综合 ffprobe(丰富信息) 与 mkvmerge(权威 Track ID)，
产出统一的 Track 列表，供检测 / 策略 / 转封装使用。

工具可用性策略
--------------
- 优先用 ffprobe（信息最丰富：声道布局 / 编码 profile / 轨道名 / 声道数）。
- 若 ffprobe 不可用（未安装 / 绿色包 tools/ 里没有 ffmpeg），**自动回退到
  mkvmerge --identify**（只要装了 MKVToolNix 即可，绝大多数环境都有）。
- 两者皆无才抛出清晰可读的错误，而不是裸的 WinError 2 堆栈。
这样即便用户的绿色包漏装了 ffmpeg，轨道预览 / 转封装依然可用。
"""
from dataclasses import dataclass, field
from typing import Optional

from . import lang_map, utils, logger


@dataclass
class Track:
    stream_index: int            # ffprobe 流序号（也用于 ffmpeg -map）
    track_id: int                # mkvmerge Track ID（用于 mkvmerge 命令）
    track_type: str              # video / audio / subtitle
    codec: str
    language_raw: str = "und"    # 原始 language tag
    language_norm: str = "und"   # 规整后的 ISO 代码（zh/cmn/eng/...）
    channels: Optional[int] = None
    channel_layout: Optional[str] = None
    profile: Optional[str] = None
    title: Optional[str] = None  # 原始轨道名
    height: Optional[int] = None  # v22: 视频高度（用于分辨率标签）
    width: Optional[int] = None   # v23.54: 视频宽度（分辨率判定宽度优先，避免 1038 误降）
    # 检测/策略结果（由后续阶段填充）
    detected_iso: Optional[str] = None       # 识别出的规范语言码
    detected_name: Optional[str] = None      # 识别出的显示名
    detected_kind: str = "unknown"           # 识别出的类型（bilingual / chinese_simplified 等）
    track_name: Optional[str] = None         # 最终写入的轨道名
    action: str = "keep"                     # keep / remove
    note: str = ""
    ocr_text: str = ""                       # OCR 原始文本预览（前 300 字）
    ocr_failed: bool = False                 # v22: OCR 识别是否失败（供跳过机制使用）


def _tracks_from_ffprobe(streams):
    tracks = []
    for st in streams:
        ctype = st.get("codec_type")
        if ctype not in ("video", "audio", "subtitle"):
            continue
        
        # 伪装 1：将单数 "subtitle" 映射为 mkvmerge 期望的复数 "subtitles"
        mapped_type = "subtitles" if ctype == "subtitle" else ctype
        
        # 伪装 2：对齐 Codec 命名规则
        raw_codec = (st.get("codec_name") or "").lower()
        if raw_codec == "hdmv_pgs_subtitle":
            mapped_codec = "hdmv/pgs"
        elif raw_codec == "ass" or raw_codec == "ssa":
            mapped_codec = "ssa/ass"
        elif raw_codec == "subrip":
            mapped_codec = "subrip"
        else:
            mapped_codec = raw_codec

        tags = st.get("tags", {}) or {}
        raw = tags.get("language") or st.get("language") or "und"
        
        stream_idx = st.get("index", 0)
        
        tracks.append(Track(
            stream_index=stream_idx,
            track_id=stream_idx,                 # 💡 核心修改：不再使用 -1，直接初始化为有效的流序号
            track_type=mapped_type,             # 使用伪装后的类型
            codec=mapped_codec,                 # 使用伪装后的 Codec
            language_raw=str(raw),
            language_norm=lang_map.coerce_lang_code(raw),
            channels=st.get("channels"),
            channel_layout=st.get("channel_layout"),
            profile=st.get("profile"),
            title=tags.get("title"),
            width=st.get("width"),
            height=st.get("height") if ctype == "video" else None,
        ))
    return tracks


def _tracks_from_mkvmerge(mkv_tracks):
    """mkvmerge -J 回退路径：用其返回的 id/type/codec/language/声道 构造 Track。"""
    tracks = []
    for mt in mkv_tracks:
        rtype = mt["type"]
        raw = mt.get("language") or mt.get("language_ietf") or "und"
        tracks.append(Track(
            stream_index=mt["id"],
            track_id=mt["id"],
            track_type=rtype,
            codec=(mt.get("codec") or "").lower(),
            language_raw=str(raw),
            language_norm=lang_map.coerce_lang_code(raw),
            channels=mt.get("channels"),
            channel_layout=None,
            profile=None,
            title=mt.get("title"),
            height=mt.get("height") if rtype == "video" else None,
            width=mt.get("width") if rtype == "video" else None,
        ))
    return tracks


def _align_with_mkvmerge(ff_tracks, mkv_tracks):
    """用 mkvmerge 的权威 Track ID 对齐 ffprobe 的轨道。"""
    if mkv_tracks:
        by_type = {"video": [], "audio": [], "subtitle": []}
        for mt in mkv_tracks:
            by_type.setdefault(mt["type"], []).append(mt)
        for t in ff_tracks:
            seq = by_type.get(t.track_type, [])
            # 找到同类型中尚未分配的最小 Track ID
            used = {x.track_id for x in ff_tracks if x.track_id != -1}
            for mt in seq:
                if mt["id"] not in used:
                    t.track_id = mt["id"]
                    t.channels = t.channels or mt.get("channels")  # 顺便用 mkv 的声道数兜底
                    break
    else:
        # 退化：以 stream_index 作为 track_id
        for t in ff_tracks:
            t.track_id = t.stream_index
    return ff_tracks


def probe_media(path):
    """解析媒体文件，返回 Track 列表。

    v21.2: 直接用 mkvmerge -J 解析（不再先尝试 ffprobe）。
    mkvmerge 信息已包含 codec/language/channels 等必要字段。
    后续抽取音频/字幕仍由 ffmpeg/mkvextract 处理，与 ffprobe 无关。
    """
    logger.log("== STAGE probe == 启动 mkvmerge -J 解析轨道", "PIPELINE")
    try:
        mkv_tracks = utils.mkvmerge_identify(path)
    except Exception as e:
        logger.log(f"mkvmerge 识别失败: {e}", "PIPELINE")
        raise utils.CmdError(
            ["probe", path], -2,
            "无法解析媒体轨道：mkvmerge 不可用。"
            "请确认 mkvmerge 已正确安装并放入 tools/mkvtoolnix/ 目录中。")

    if not mkv_tracks:
        raise utils.CmdError(
            ["probe", path], -1,
            "mkvmerge 返回的轨道列表为空")

    return _tracks_from_mkvmerge(mkv_tracks)

def split_tracks(tracks):
    """按类型分组。"""
    return (
        [t for t in tracks if t.track_type == "video"],
        [t for t in tracks if t.track_type == "audio"],
        [t for t in tracks if t.track_type == "subtitle"],
    )


def summarize(tracks):
    """生成人类可读的轨道摘要（用于 GUI 预览）。"""
    aud, sub = [], []
    for t in tracks:
        if t.track_type == "audio":
            aud.append(f"#{t.track_id} {t.codec} lang={t.language_raw or 'und'}"
                       + (f" name='{t.track_name}'" if t.track_name else ""))
        elif t.track_type == "subtitle":
            sub.append(f"#{t.track_id} {t.codec} lang={t.language_raw or 'und'}")
    return "\n".join(["[音频]"] + (aud or ["(无)"]) +
                     ["[字幕]"] + (sub or ["(无)"]))


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        for t in probe_media(sys.argv[1]):
            print(t)