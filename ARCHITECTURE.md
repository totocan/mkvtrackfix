# MediaMetaFixer (mkvtrackfix) 架构与流程说明

> 版本: v23.25 | 最后更新: 2026-07-21

---

## 一、文件导入

### 1.1 浏览文件夹（批量）

```
按钮 → browse_folder()
  → QFileDialog.getExistingDirectory() 选文件夹
  → self.le_path.setText(p)
  → self.collect()
    → pipeline.collect_files(src, recursive, extensions)
      → os.walk() 遍历目录（recursive=True 递归 / False 仅当前层）
      → 按扩展名过滤（默认 .mp4 .mkv）
      → 返回 sorted(files)
    → self.files = files     ← 替换整个列表
    → self.results.clear()   ← 清空旧结果
    → self._fill_table()
```

**行为：** 选文件夹 → 自动扫描 → 替换整个文件列表。不支持追加。

### 1.2 浏览文件（多选追加）

```
按钮 → browse_file()
  → QFileDialog.getOpenFileNames() 多选文件
  → 去重：existing = set(self.files); new = [f for f in files if f not in existing]
  → self.files.extend(new)          ← 追加到现有列表
  → self._fill_table()
```

**行为：** 可多选 → 逐个追加（去重）→ **不替换**已有文件。

### 1.3 拖入文件（单个追加）

```
dropEvent → add_path(p)
  → if p not in self.files: self.files.append(p)
```

**行为：** 拖入一个追加一个。去重。不替换已有文件。

### 1.4 导入记录（断点续传）

```
按钮 → load_record()
  → 选择 .json 记录文件
  → 解析 JSON v2 格式
  → 恢复 self.files / self.results（Track 对象列表）
  → 恢复 _completed（done=True 的已扫描/已处理文件）
  → _fill_table() + 恢复计划动作列 + 状态列 + 绿色已完成标记
  → v23.20+: 兼容老记录，status="已分析"也视为已完成
```

---

## 二、扫描流程（Scan）

### 2.1 整体流程

```
用户点击「扫描预览」
  → MainWindow.do_scan()
    → _start_worker("scan")
      → 传入 skip_done_paths（已完成文件列表）
      → Worker(self.files, self.results, cfg, mode="scan", skip_done_paths)
      → self.cache = CacheManager(...).start()
        → 后台线程预缓存 tmp/1/, tmp/2/, tmp/3/...
        → 提前 2 个：任务 N 开始，缓存 N+1, N+2
      → 循环 for i, f in enumerate(self.files):
        → file_start.emit(i)     ← 表格行高亮蓝色
        → [跳过检测] 已在 skip_done_paths → "已完成(自动跳过)"
        → cache.ensure(i)        ← 确保本地缓存就绪
        → pipeline.analyze_file(src, config, orig_path=f, temp_dir=tmp_i)
          ┌─────────────────────────────────────┐
          │ 1. probe.probe_media(path) 轨道解析  │
          │ 2. douban.classify_movie() 产地判断  │
          │ 3. 批量提取所有音轨 WAV 段           │
          │ 4. 批量提取所有字幕文件               │
          │ 5. AI 识别音轨（投票）               │
          │ 6. OCR/文本检测字幕                  │
          │ 7. policy.apply 策略（保留/移除）     │
          └─────────────────────────────────────┘
        → file_done.emit(row, plan, status, level)
        → cleanup_sliding(i)     ← 清理旧缓存
```

### 2.2 轨道解析（probe.probe_media）

```
调用 mkvmerge -J <file>
  → JSON 输出 → 解析 tracks 数组
  → 过滤 video/audio/subtitle
  → 提取：id, type, codec, language, properties
  → 返回 Track 对象列表
```

**已知限制：** 仅支持 MKV 格式（mkvmerge 只能处理 MKV）。  
MP4 文件没有内置轨道容器信息，无法直接解析。  
如果传入 mp4，会尝试用 ffmpeg 探测，但可能降级。

### 2.3 产地判断（classify_movie）

> 代码文件为 `core/douban.py`，但已替换为 TMDB（v21.2 起）。  
> 文件名是历史遗留，功能已是 TMDB 纯网页抓取，不需要 API Key。

```
输入：视频文件的文件名 + 路径
  → 正则解析：提取英文电影名、年份
  → themoviedb.org 搜索 → 正则提取 movie_id
  → themoviedb.org/movie/{id} 详情 → 正则提取国家代码 + 默认语言
  → 根据国家代码判定是否国产，根据语言判定原生语言
  → 返回 movie_info:
    - is_domestic: bool（是否国产）
    - native_lang_name: str（原生语言名）
    - native_lang: str（原生语言 ISO）
    - source: str（信息来源：tmdb / search_miss / unknown）

配置项:
  douban_enabled = true  ← 控制 TMDB 查询开关（文件名历史遗留）
  关闭后降级到启发式推断（根据文件名/路径猜测产地）
```

用于后续策略：国产电影去掉英语音轨，外国电影保留英语+普通话。

---

## 三、音轨检测（Audio Detection）

### 3.1 提前返回判断（\_audio_early_check）

```
配置项: audio_redetect = "all" / "und_only" / "skip"

skip 模式:
  → 已有语言标签 → 直接使用，不检测
  → 标签为 und → 启发式推断（从文件名/路径推断）

und_only 模式:
  → 已有非 und 标签 → 直接使用
  → 标签为 und → 继续检测

all 模式（默认）:
  → 全部继续检测（detected_iso 设为空）
```

### 3.2 音轨采样（\_extract_audio_segments）

```
配置项:
  sample_segments = "600,1000,1500"  ← 采样起点（秒），逗号分隔
  sample_duration_seconds = 10        ← 每段采样时长

流程:
  for each start_sec in sample_segments:
    → ffmpeg 截取 [start, start+duration] 区间的音频
    → 输出 WAV 文件到 temp_dir
    → 命名: audio{track_id}_seg{i}_{start}s.wav

默认 3 段：600s(10min), 1000s(16.7min), 1500s(25min)
每段时长可在设置中选择 10/15/20 秒（默认 10 秒）。
注意：低于 10 秒会导致 Whisper AI 识别不稳定（子进程崩溃），故不提供 5 秒选项。
```

### 3.3 音轨 AI 检测（\_detect_audio）

```
流程:
  1. 尝试 ffmpeg concat 合并所有 WAV 段为一个 combined.wav
  2. 对 combined.wav 运行 audio_detect.detect()
     → 使用 Whisper 模型（medium 默认）识别语言
     → 返回 {iso, zh, dominant_ratio}
  3. 如果合并失败，逐段识别：
     → 每段 WAV 单独识别 → 投票（票数最多的语言胜出）
  4. 投票决策：
     → 有票 → 取最高票语言
     → 无票 → 启发式推断（从文件名/路径推断）
  5. 非调试模式：删除提取的 WAV 文件
```

**采样配置（config.json）：**

| 参数                        | 默认值               | 说明                                         |
| ------------------------- | ----------------- | ------------------------------------------ |
| `sample_segments`         | `"600,1000,1500"` | 采样起点（秒），可自定义如 `"300,600,900,1200"`         |
| `sample_duration_seconds` | `10`              | 每段采样时长                                     |
| `model_size`              | `"medium"`        | Whisper 模型：tiny/base/small/medium/large-v3 |
| `audio_redetect`          | `"all"`           | all / und_only / skip                      |
| `zh_audio_as`             | `"cmn"`           | zh 映射：cmn / yue                            |

---

## 四、字幕检测（Subtitle Detection）

### 4.1 字幕提取（extract_only）

```
for each subtitle track:
  → mkvextract 提取字幕到本地文件
  → 文本字幕：sub_{id}.srt / .ass / .ssa
  → 图像字幕：sub_{id}.sup（PGS/BDN XML）
```

### 4.2 文本字幕检测

```
if 文本字幕（srt/ass/ssa）:
  → 直接读取文本内容
  → classify_subtitle_text(text)
    → 语言分类引擎
    → 检测简中/繁中/英文/其他
    → 简中+英文 → "chinese_bilingual"
    → 仅英文 → "english"
    → 繁中 → "chinese_traditional" → 标记移除
  → 保存 OCR 文本预览（前 300 字符）
```

### 4.3 图像字幕 OCR（PGS 字幕）

图像字幕需要从视频帧中 OCR 识别文字。使用 ffmpeg 叠加字幕轨道到黑色背景上，渲染为 PNG 帧，再用 RapidOCR 识别。

#### 4.3.1 采样策略

```
配置项:
  ocr_skip_seconds = 300    ← 跳过前 N 秒（避开片头）
  ocr_max_attempts = 4      ← 最多尝试次数
  per_attempt_duration = 30  ← 每次持续 30 秒

采样方案（v23.25）:
  attempt_starts = [300, 600, 900, 1200]   ← 每 300 秒一次

改前（v23.22 之前）:
  attempt_starts = [300, 330, 360, 390]    ← 每 30 秒一次（bug，间隔太密）

每个尝试点:
  → ffmpeg: 黑色背景 + 字幕叠加 → 输出 PNG 帧（1fps）
  → frame_dir = temp_dir/ocr_sub{sub_id}/
  → 30 秒 → 约 30 帧 PNG
```

#### 4.3.2 OCR 去重与采样

```
_sample_ocr_frames(frame_paths, max_frames=30):
  1. 帧数 ≤ 30 → 全部保留
  2. 否则先均匀采样到 60 帧
  3. (文件大小, 中间4KB) hash 去重
  4. 去重后仍 > 30 → 均匀缩到 30 帧

→ RapidOCR 识别这组帧
→ 如果文本长度 ≥ ocr_min_text_len(30) → 成功
→ 否则尝试下一个采样点
→ 4 次全失败 → 启发式推断
```

#### 4.3.3 帧目录清理

```
非调试模式 + 非 keep_ocr_frames:
  → 每次尝试前删除旧帧
  → 成功识别后删除 frame_dir
  → 4 次全失败也删除

调试模式 或 keep_ocr_frames=True:
  → 保留 frame_dir，不做任何清理
```

### 4.4 字幕配置参数

| 参数                         | 默认值     | 说明                    |
| -------------------------- | ------- | --------------------- |
| `ocr_skip_seconds`         | `300`   | 跳过前 N 秒（避开片头）         |
| `ocr_max_attempts`         | `4`     | 最多尝试次数                |
| `ocr_min_text_len`         | `30`    | OCR 有效文本最小长度          |
| `subtitle_extract_timeout` | `180`   | 字幕抽取超时（秒）             |
| `keep_ocr_frames`          | `false` | 保留 OCR 帧/WAV（独立于调试模式） |

---

## 五、策略（Policy）

### 5.1 音轨策略（apply_audio_policy）

```
语言优先级: cmn > yue > eng > 其他

国产电影（is_domestic=True）:
  → 保留 cmn / yue
  → 去掉 eng / 其他

外国电影（is_domestic=False）:
  → 保留 eng / cmn
  → 去掉其他

同语言多音轨:
  → 按 声道数 × 编码权重 评分
  → 保留最高的，移除其余的

兜底: 所有音轨都不在优先级列表 → 保留声道数最好的
约束: 至少保留 1 条音轨
```

**编码质量权重：**

| 编码            | 权重 |
| ------------- | -- |
| TrueHD Atmos  | 10 |
| TrueHD        | 9  |
| FLAC          | 8  |
| DTS-HD MA     | 7  |
| AC-3 / E-AC-3 | 5  |
| AAC           | 3  |
| 其他            | 2  |

### 5.2 字幕策略（apply_subtitle_policy）

```
优先级:
  1. 简中英双语 → 保留（最高优先级）
  2. 独立简体中文 → 没有双语时保留；有双语视为冗余移除
  3. 纯英文 → 没有简中也没有双语时保留
  4. 繁体中文 / 其他语言 → 移除
  5. 未知类型 → 保守保留

配置项:
  sub_remove_traditional: true             → 移除繁中
  sub_remove_pure_english_if_bilingual: true  → 有双语时移除纯英文
  sub_remove_redundant_simplified_if_bilingual: true → 有双语时移除冗余简中
```

---

## 六、处理流程（Process/Remux）

### 6.1 整体流程

```
用户点击「开始处理」
  → MainWindow.do_process()
    → 检查未保存修改 → 提示保存
    → _start_worker("process")
      → 传入 skip_done_paths（已完成处理的文件列表）
      → Worker(self.files, self.results, cfg, mode="process")
      → 循环 for i, f in enumerate(self.files):
        → [跳过检测] 已在 skip_done_paths → "已完成(自动跳过)"
        → cache.ensure(i) → 确保本地缓存就绪
        → if f in self.results（已有扫描结果）:
            pipeline.process_tracks(src, tracks, run_cfg)
              → remux.remux(tracks, src, config)
                → build_command → mkvmerge -o 转封装
                → 带重命名 / .fixed 后缀
            → 输出 tmp/N/ → _relocate_output 搬回 NAS
        → else（无扫描结果，直接处理）:
            pipeline.process_file(src, run_cfg)
              → analyze_file(...)   ← 先扫描
              → remux.remux(...)    ← 再处理
            → 同上搬回 NAS
```

### 6.2 mkvmerge 命令构造（build_command）

```
基础: mkvmerge -o <输出> --track-order ...
  → 设置语言标签：--language 0:cmn --language 1:eng
  → 设置轨道名称：--track-name 0:简体中文
  → 移除音轨：--audio-tracks !1,!3
  → 移除字幕：--subtitle-tracks !2,!4
  → 保留的视频/音频/字幕重新排序
```

### 6.3 输出路径

```
compute_output_path(src, config, tracks):
  if smart_rename:
    → namer.generate_name() → 基于电影名+年份+分辨率+编码的智能命名
  else:
    → 原文件名 + output_suffix + .mkv

输出位置：
  → v23.18+: 写入 tmp/N/，然后 _relocate_output 搬回 NAS
  → 搬回逻辑（v23.27 修复）：使用 namer 输出名的 basename，不再强制 .fixed 后缀
```

### 6.4 智能重命名规则

```
[中文名.]英文名.年份.国家.分辨率.视频编码.音频编码.声道.fixed.mkv

示例：
  A.Fistful.of.Dollars.1964.意大利.1080p.H.265.FLAC.2.0.fixed.mkv
  黄金三镖客.1966.意大利.1080p.H.264.DDP.5.1.fixed.mkv
  七武士.1954.日本.1080p.H.264.FLAC.1.0.fixed.mkv
```

**说明：**
- **不加入蓝光/杜比视界/HDR 等信息**：主流播放器（芝杜、极影视、Kodi、Jellyfin）默认就能识别媒体文件的 HDR/DV 等元数据，完全无保留必要
- **文件名主要为刮削识别服务**：刮削依赖片名 + 年份，加入冗余的格式标记反而干扰
- **国家**：从 TMDB 查询的中文国家名（如"美国""日本""意大利"），用于区分同名电影（不同国家可能同名），TMDB 查不到时不加入
- **中文名策略**：
  - 父目录已含中文名 → 文件名不重复加中文名（减少长度）
  - 父目录无中文名 → 文件名加入 TMDB 中文名，排列为 `中文名.英文名.年份…`
  - 用户（如极影视）查看文件列表时，路径或文件名总有一处显示中文，方便手动刮削
- **分辨率和编码保留**：用于快速识别清晰度和兼容性（1080p/2160p + H.264/H.265/AV1）
- **音频编码和声道保留**：用于识别音质和声道布局（FLAC/DDP/AC-3 + 2.0/5.1/7.1）

---

## 七、缓存架构（Caching）

### 7.1 目录结构

```
tmp/
├── 1/                          ← 任务 1（1-based）
│   ├── movie.mkv              ← 整片缓存（从 NAS 复制）
│   └── temp/                  ← 该任务的提取副产品
│       ├── audio1_seg0_600s.wav
│       ├── audio2_seg0_600s.wav
│       └── ocr_sub3/
│           ├── frame_0001.png
│           └── ...
├── 2/ ...
├── 3/ ...
└── debug_last/                ← 调试快照
```

### 7.2 预缓存策略

```
任务 N 开始 → 缓存 N+1, N+2
  → 后台线程（daemon）
  → 逐文件复制（4MB 分块）
  → 先写 .tmp，完成后 rename 到最终路径
  → skip_set 中的已完成文件跳过预缓存
```

### 7.3 滑动窗口清理

```
完成任务 N（0-based）:
  → 清理 N-2 及更早的目录
  → 保留 N 和 N-1
  → keep_ocr_frames=True 时：
    只删视频缓存文件，保留 temp/ 子目录
```

### 7.4 调试快照

```
配置项: debug_mode = true/false
       keep_ocr_frames = true/false

失败 + 调试模式:
  → 当前任务的 tmp/{i+1}/ 移栽到 debug_last/
  → 供离线排查

磁盘感知:
  → 磁盘剩余 < 5GB 时自动清理旧快照
  → 保留最新的一个快照
```

---

## 八、配置参数总表

| 参数                                             | 默认值               | 作用域 | 说明                         |
| ---------------------------------------------- | ----------------- | --- | -------------------------- |
| `model_size`                                   | `medium`          | AI  | Whisper 模型大小               |
| `device`                                       | `cpu`             | AI  | cpu / cuda                 |
| `compute_type`                                 | `int8`            | AI  | int8/int16/float16/float32 |
| `sample_segments`                              | `"600,1000,1500"` | 音轨  | 采样起点（秒）                    |
| `sample_duration_seconds`                      | `10`              | 音轨  | 每段时长                       |
| `ocr_skip_seconds`                             | `300`             | 字幕  | 跳过前 N 秒                    |
| `ocr_max_attempts`                             | `4`               | 字幕  | 最大尝试次数                     |
| `ocr_min_text_len`                             | `30`              | 字幕  | 有效文本最小长度                   |
| `audio_redetect`                               | `all`             | 音轨  | all/und_only/skip          |
| `zh_audio_as`                                  | `cmn`             | 音轨  | cmn/yue                    |
| `subtitle_extract_timeout`                     | `180`             | 字幕  | 提取超时                       |
| `sub_remove_traditional`                       | `true`            | 策略  | 移除繁中                       |
| `sub_remove_pure_english_if_bilingual`         | `true`            | 策略  | 有双语时去英文                    |
| `sub_remove_redundant_simplified_if_bilingual` | `true`            | 策略  | 有双语时去冗余简中                  |
| `audio_reduce`                                 | `true`            | 策略  | 同语言多轨精简                    |
| `audio_keep_best_only`                         | `true`            | 策略  | 保留最佳音轨                     |
| `smart_rename`                                 | `true`            | 输出  | 智能重命名                      |
| `output_suffix`                                | `".fixed"`        | 输出  | 非智能重命名后缀                   |
| `douban_enabled`                               | `true`            | 产地  | 豆瓣查询                       |
| `domestic_drop_english`                        | `true`            | 策略  | 国产去英音轨                     |
| `verbose_tools`                                | `true`            | 日志  | 第三方工具日志                    |
| `debug_mode`                                   | `false`           | 调试  | 保留临时文件                     |
| `keep_ocr_frames`                              | `false`           | 调试  | 保留 OCR 帧/WAV               |
| `recursive`                                    | `true`            | 导入  | 递归扫描目录                     |
| `extensions`                                   | `["mp4","mkv"]`   | 导入  | 文件扩展名过滤                    |

---

## 九、版本演进速览

| 版本     | 核心变更                               |
| ------ | ---------------------------------- |
| v23.15 | 调试模式磁盘修复（滑动窗口清理）                   |
| v23.16 | 记录编辑器（删除行 + 断点续传）                  |
| v23.17 | 扫描模式断点续传                           |
| v23.18 | **恢复整片缓存 tmp/N/**（CacheManager 复活） |
| v23.19 | 修复缓存文件名空格 + 断点续传检测                 |
| v23.20 | 兼容老记录 + 启动清理旧缓存                    |
| v23.21 | 背景缓存跳过已完成的文件                       |
| v23.22 | OCR 采样改 300 秒间隔                    |
| v23.23 | keep_ocr_frames 加入设置界面             |
| v23.24 | 更新 CHANGES.md + 修复 purge 顺序        |
| v23.25 | 区分扫描完成与处理完成（断点续传修复）                |
