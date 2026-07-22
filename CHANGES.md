# MediaMetaFixer 变更说明

## 🔧 v23.55 — TMDB 管理器强化：年份档位 + 类型中文 + 高速档 + 崩溃日志

### 泛搜索（🔍 标签页）
- **年份筛选改为下拉档位**：`全部年份 / 1910年以前 … 2010年以前（每10年一档）/ 2015 / 2020 / 2025 / 2030 年以前`（X年以前 = year <= X）
- **类型下拉显示中文**：`distinct_genres()` 用 GENRE_MAP 把 Kaggle 英文名（Action/Drama…）转中文（动作/剧情…）；筛选支持中英文双向匹配
- 类型/年份筛选在 `search_broad` 内统一生效，分级匹配（[精确]/[同年]/[模糊]）不受影响

### 自动强化（🕷 标签页）
- **速度档位扩展为 10 档**：`1秒50条 / 1秒30条 / 1秒20条 / 1秒10条 / 1秒1条 / 5秒1条 / 10秒1条 / 15秒1条 / 20秒1条(默认) / 30秒1条`
- **429 限流自动退避**：高速档打满 TMDB 上限时，按 `Retry-After` 或 5 秒退避重试，不中断
- **🌐 转中文国名改为后台线程**：大库不再卡死 UI，带进度日志（与爬取速度无关，纯本地静态映射）

### 稳定性
- **独立日志文件** `logs/tmdb_manager.log`：界面日志双写，所有操作可追溯
- **`sys.excepthook` 全局捕获**：任何未处理异常写入日志，不再"默默退出"无迹可寻
- 修复 `year_max` 范围筛选未生效、Kaggle `genres` 为 JSON 字符串未二次解析导致类型筛选失败的 bug

---

## 🔧 v23.54 — 编码简写统一 + 命名单一来源 + 4 项 Bug 修复

### 核心重构（对齐「一个功能一个 py 文件」）
- 新增 `core/codec.py`：**编码显示名 / 文件名简写 / 质量权重** 的单一事实来源，
  彻底消除此前 `lang_map._CODEC_DISPLAY` 与 `namer._AUDIO_CODEC_SIMPLE` 两套不一致映射
- 新增 `core/lang.py`：从 `lang_map.py` 抽取纯语言系统（语言表/简繁/分类/推断）
- 新增 `core/naming.py`：`make_audio_track_name` / `get_subtitle_track_name` /
  `generate_name` / `_track_name_audio_info` 统一实现
- `lang_map.py` / `namer.py` 改为兼容转发层，旧调用方无需改动

### 修复（用户反馈的核心问题）
- **编码简写失效**：此前 `namer._AUDIO_CODEC_SIMPLE` 键为全拼小写（`"dts hd master audio"`），
  而轨道名里实际是 `"DTS-HD MA"`，永远匹配不上 → 文件名音频编码常为空。
  现统一走 `codec.short_name`，扫描模式与仅重命名模式使用同一映射
- **TrueHD Atmos 被吞成 TrueHD**：文件名简写丢失 Atmos 标记，
  现 `codec.short_name("truehd","atmos")` → `"TrueHD.Atmos"`
- **仅重命名不按规则**：仅重命名模式原本直接读 mkvmerge 原始 track_name，
  未走规范命名链路；现统一经 `naming` 模块，与扫描模式一致

### Bug 修复
1. `remux._lang_match`：`got_lang_lower`/`got_ietf_lower` 未定义导致中文校验 NameError → 已补变量
2. `remux.build_command`：内部字幕 `--subtitle-tracks` 重复输出，且外挂字幕 id 误当主文件 track id
   → 仅内部字幕输出一次，`--no-subtitles` 仅当无内部且无外挂时
3. `douban.classify_movie`：TMDB 缓存命中时引用未定义 `_LANG_NAMES` → 改用 `lang.lang_info_by_iso`
4. `policy.apply_subtitle_policy`：外挂字幕未被排除去重策略 → 循环开头跳过，默认保留

### 分辨率判定统一（v23.54 新增 `codec.resolution_label`）
- 修复 `1920x1038` 等变形/裁切高度被硬阈值（`height>=1080`）误降为 720p 的问题
- 规则：**标准档位 + 容差对齐（±10%）+ 宽度优先 + 短板约束（0.7 下限）**
  - 宽度优先：1080p 内容宽度几乎都是 1920，用宽度定档比高度更稳
  - 短板约束：高度断崖（如 `1920x720`）判定为扁平宽屏，降级到 720p
  - 最近邻兜底：`720x404` 等 WebRip 取最接近标准档
- **4K 特例**：最高档（2160p）宽度命中即认 2160p，不受高度断崖影响
  （`3840x1080` 等极端超宽属 4K 内容，用户认可为 2160p）
- `probe.py` 补 `width` 字段解析；`naming.generate_name` 与 GUI 表格展示统一调用，
  **展示 / 命名 / 仅重命名三处分辨率判定完全一致**

### TMDB 管理器强化（v23.54 新增两个标签页）
- **🔍 泛搜索**：输入可含 `.` 分隔符（如 `casino.royale.1967`），自动归一化（转空格、剥离内嵌年份、
  防 `casino.royale` 粘连成 `casinoroyale`）；三级分级匹配并标注
  `[精确]`（标题+年份全对齐）/ `[同年]`（标题同、年份不同）/ `[模糊]`（标题包含/被包含）；
  支持**年份 / 国家 / 类型**筛选；结果**分页（每页 100，可翻页）**
- **🕷 自动强化**：不依赖主界面扫描，工具自己强化本地 `.db` 数据
  - 用 **TMDB 官方 API**（v3 key，存 config.json）批量补 `title_zh` + `country_name`
  - 爬取间隔可选 **5/10/15/20/30 秒/条（默认 20）**，可挂机后台、可随时停止
  - 🌐 **转中文国名**按钮：用内置 ~100 国 `ISO3166 → 中文名` 静态表零成本补全 `country_name`，
    不联网
  - 核心目标：把 Kaggle 导入时缺失的中文名（`title_zh`）慢慢补齐
- `core/tmdb_cache.py` 配套新增：`search_broad()`（分级+分页+筛选）、
  `apply_country_names()`（静态映射补国名）、`strengthen_missing()`（API 批量补中文名）、
  `COUNTRY_MAP` / `GENRE_MAP` 静态映射表

---

## 🐛 v23.53 — 缓存写入验证 + 文件自检 + 外挂字幕

### 修复
- 缓存写入后先 `open().read(16)` 验证可读，再标记就绪（解决 rc=-2 崩溃）
- `mkvmerge_identify()` 调 mkvmerge 前先自行检查文件可读性
- utils.py 路径归一化，避免扫描结果处理时重复分析

### 新增
- 扫描时自动收集同目录 `.srt/.ass/.ssa/.vtt` 外挂文本字幕
- 智能语言推断（读文件头 → 双语/简中/英文）
- 处理后自动合并到输出文件（带语言标签和轨道名）
- 设置右侧面板「合并外挂文本字幕」复选框
- TMDB 本地缓存子系统（`core/tmdb_cache.py`）
- 独立 GUI 管理器（`tmdb_manager.py` / `tmdb_manager.bat`）
- 托盘菜单「TMDB 缓存管理器」直接启动
- 推送改为 ServerChan（无需 IP 白名单）
- `ensure()` 缓存验证失败后重试 3 次（每次 2 秒）

---

### 修复
- `ensure()` 缓存验证失败后重试 3 次（每次 2 秒），解决后台写锁导致 mkvmerge rc=2
- `CacheManager._log()` 传参修复（去掉多余 level 参数）
- AI 子进程崩溃时输出系统快照：内存%、子进程数、句柄数、失败累计

---

### 新增
- **仅智能重命名**：不缓存、不扫描、不处理，纯改名
  - 遍历文件列表 → mkvmerge -J 取元数据 → TMDB 查询 → namer 生成规范名 → os.rename
  - 零磁盘写入，改的是 NAS/本地原文件，不改内容
  - 目标存在时自动加 `(1)` 后缀，不覆盖
  - TMDB 查不到或解析失败则原样跳过
- 按钮位于「仅保留有问题的」右侧

### UI
- 设置窗口高度 +70px (900→970)，按钮与感谢语间距增加 40px
- 设置窗口「?」按钮已移除

---

### 新增
- **关闭防护**：任务进行中点窗口 X 不再退出，日志提示；菜单「退出」始终可用
- **自动保存**：每完成一个任务自动写入 `records/mmf_autosave.json`，日志绿色显示
  `✓ 任务 12/128 已完成(用时45秒)，记录已自动保存`
- 音轨编码名简化映射表，文件名中的长编码缩短：
  - `DTS-HD MASTER AUDIO` → `DTS-HD.MA`
  - `DTS-HD HIGH RESOLUTION` → `DTS-HiRes`
  - `DOLBY DIGITAL PLUS` → `DOLBY.DiPlus`
  - `E-AC-3` → `E-AC3`
  - `TRUEHD ATMOS` → `TrueHD`

### 修复
- `config.load()` 版本升级时不再重置所有配置（改为保留用户设置 + 补充新默认键）
- 主窗口右上角 X 按钮已移除（`WindowCloseButtonHint`）
- 设置窗口右上角「?」按钮已移除（`WindowContextHelpButtonHint`）

---

## 🐛 v23.44 — 修复设置 Qt 导入冲突 + 帮助菜单分离 GitHub/知乎

### 修复
- 打开设置报错 `UnboundLocalError: Qt` — `_build()` 中重复的 `from PyQt5.QtCore import Qt` 导致 Python 将其视为局部变量

### 菜单调整
- 帮助菜单「作者主页」→「作者知乎主页」（地址不变）
- 新增「作者 GitHub 主页」链接到 `github.com/totocan`

---

### UI 调整
- 界面字体设置从左侧移到右侧面板（在预缓存上方）
- 预缓存提前量改为下拉框 `[2, 3, 5, 10, 15, 20]`，默认 2
- 右侧字号缩小（标题 18pt / 副标题 11pt / 感谢语 12pt）
- 设置窗口加宽至 1800×900，左右比例 6:4
- 全局 Qt 中文翻译加载（滚动条 Tooltip 等变中文）

### 新增
- 预缓存提前量可配置（设置右侧面板）
- OCR 每段超时下拉 `[45, 60, 75, 90]` 秒，默认 60（原写死 45）

### 修复
- 缓存 MKV/MP4 文件头验证（magic bytes），拦截损坏缓存
- `_relocate_output` 不再覆盖 namer 生成的智能命名
- `cache.current_idx` 从未设置 → 预缓存实际从未工作

---

## 🐛 v23.42 — OCR 超时配置 + 预缓存配置

### 新增
- `ocr_attempt_timeout` 配置，每段 OCR 尝试超时下拉 `[45,60,75,90]`，默认 60
- `prefetch_ahead` 配置，预缓存提前量可设置（1~20，默认 2）
- `subtitle_extract_timeout` 默认从 180 改为 90 秒

---

## 🐛 v23.41 — 右侧字体改用 QFont（CSS px 失效修复）

### 修复
- 改为 `QFont("Microsoft YaHei", 18, QFont.Bold)` 的 pt 点单位，真实控制渲染大小

---

## 🐛 v23.38 — 设置左右比例 6:4 + 移除 embed_qr.py

### UI 调整
- 右侧面板移除 `setFixedWidth(340)`，改为 `h_split.addWidget(right_panel, 4)`
- 左侧 `stretch=6`，右侧 `stretch=4`，窗口缩放时可自适应

### 仓库清理
- `embed_qr.py` 从项目根目录移除，放入 `/workspace/` 供作者个人使用
- 后续打包不再包含此脚本

---

## 🐛 v23.36 — 修复预缓存未工作（cache.current_idx 从未设置）

### 问题
处理任务 14 时，tmp/ 下只有 1~14，没有 15、16。始终只有前两个文件被预缓存。

### 根因
`Worker.run()` 的循环内部从未设置 `self.cache.current_idx = i`，导致 CacheManager 后台线程读到的 `_current_idx` 恒为 -1，只缓存了文件 0 和 1。

### 修复
在 `for i, f in enumerate(self.files):` 内、`file_start.emit(i)` 之后加一行 `self.cache.current_idx = i`。后台线程据此判断 curr，确保 tmp/N+1/、tmp/N+2/ 提前就绪。扫描和处理模式同时受益。

---

## 🐛 v23.35 — 缓存 MKV 头验证加固

### 缓存加固
- `ensure()` 之前仅验证 `os.path.getsize > 0`，损坏但非零的缓存文件仍被放行 → mkvmerge rc=2
- 新增 `_is_valid_media()` 方法，读取文件前 16 字节检查 MKV EBML 头（`\x1A\x45\xDF\xA3`）或 MP4 ftyp box
- 验证失败自动删除缓存 → 前台同步重缓存 → 重缓存仍失败则返回 None 跳过任务

---

## 🐛 v23.31 — 去掉 5 秒采样选项

### 修复
- 下拉框去掉 5 秒选项（`[5,10,15,20]` → `[10,15,20]`）
- 5 秒音频导致 Whisper 子进程崩溃（音频过短触发 CTranslate2 边界条件）

---

## 🐛 v23.30 — ensure 缓存验证 + 自动重缓存

### 修复
- `ensure()` 返回缓存前验证文件存在且非空
- 损坏/空的缓存自动删除并重新拷贝
- 去掉 v23.29 的 try-catch 回退直读方案（直读 NAS 无实际意义，且同样消耗网络带宽）

---

## 🐛 v23.29 — 缓存文件不可用时自动回退直读 NAS（后废弃）

---

## 🐛 v23.28 — 文件名加入国家（来自 TMDB）

### 新增
- 智能重命名增加国家字段：`[中文名.]英文名.年份.国家.分辨率…`
- 扩充 `_COUNTRY_NAMES` 至 50+ 国家映射（中美日韩英法德意西…）
- TMDB 查不到国家时不加入

---

## 🐛 v23.27 — 智能重命名生效（_relocate_output 修复）

### 问题
扫描后点处理，输出文件名变成 `原文件名.fixed.mkv`，namer 生成的智能名称被忽略。

### 原因
`_relocate_output` 重新用 `obase + suffix` 计算 target，覆盖了 namer 的输出名。

### 修复
`_relocate_output` 改用 `out_path` 的 basename（即 namer 的输出名），不再覆盖。

---

## 🐛 v23.26 — 采样时长下拉框 + 表格自动滚动 + 架构文档

### UI 改进
- 音轨采样时长：QSpinBox(10~600) → **QComboBox[5/10/15/20]秒**
- **表格自动滚动**：新任务开始自动滚动到该行；用户手动滚屏后 60 秒无操作恢复自动滚动

### 新增文档
- `ARCHITECTURE.md`：完整的架构与流程说明

### 修复
- **扫描后点「开始处理」不再全部跳过**：之前 `_completed` 未区分"已分析"(扫描完成)和"完成"(处理完成)，扫描完保存记录再点处理，59 个文件全部误判为"已完成"→0 秒结束
- `_start_worker` 中 process 模式只跳过状态含"完成"且不含"已分析"的文件

---

## 🐛 v23.24 — 更新 CHANGES.md + 修复 purge 顺序

### 新增
- 「保留OCR帧」勾选框加入**设置 → 日志与调试**，勾上即可生效，无需改 config.json
- 独立于调试模式：不开调试也能保留 OCR 截图/音轨 WAV 供排查

---

## 🐛 v23.22 — OCR 采样间隔修正 + keep_ocr_frames

### 修复
- OCR 采样从 `[300, 330, 360, 390]` 改为 **`[300, 600, 900, 1200]`**，真正每 300 秒采样一次

### 新增
- `keep_ocr_frames` 配置项（默认关闭）
  - 开启后滑动窗口**只删视频缓存文件**，保留 `tmp/N/temp/` 下的 OCR 帧 PNG 和音轨 WAV
  - `clean_frame_dir` 独立受控，不再依赖调试模式

---

## 🐛 v23.21 — 背景缓存跳过已完成文件

### 修复
- `CacheManager._preload_one` 增加 `_skip_set` 检查，已完成文件不再被后台预缓存线程浪费硬盘
- 扫描跳过时增加日志输出 `跳过(已分析): xxx`，不再静悄悄

---

## 🐛 v23.20 — 兼容老记录 + 启动清理旧缓存

### 修复
- `load_record` 增加「已分析」状态回退检测：老记录（v23.18 之前保存的）也能正确跳过已完成
- `_purge_stale_temp_on_start` 增加清理 `tmp/N/` 数字子目录，防止 v23.18 残留的损坏缓存被本轮直接复用

---

## 🐛 v23.19 — 修复断点续传检测 + 缓存文件名空格

### 修复
- `save_record` 中 `done` 检测增加「已分析」识别（扫描模式断点续传生效）
- `CacheManager.local_path` 将空格替换为下划线，避免文件名带空格时 mkvmerge 报错

---

## 🎯 v23.18 — 恢复本地整片缓存架构（tmp/N/）

### 设计目标
v22 的整片缓存被移除后（v23.15 改为只读 NAS + 共享 `tmp/temp/`），每个视频的音频段提取、字幕提取、remux 都要反复走网络，实际测量网络开销比本地缓存多约 4 倍。

### 恢复方案
- **CacheManager 复活**：后台线程预拉取整片到 `tmp/N/`，pipeline 全程读本地
- **预缓存策略**：任务 N 开始时确保 N+1、N+2 均已就绪（提前 2 个）
- **滑动窗口**：完成任务 N 后清理 N-2，保留当前 + 前 1 个
- **磁盘感知快照**：失败产物移入 `debug_last/`，磁盘 < 5GB 时逐级清理旧快照
- **`pipeline.py`**：`analyze_file` / `process_file` 接受 `temp_dir` 参数（任务级 `tmp/N/temp/`）
- **`_relocate_output` 激活**：remux 输出从 `tmp/N/` 搬回 NAS

### ⚠️ 后续修复
v23.18 引入了缓存文件名空格问题和断点续传检测缺失，已在 v23.19~v23.23 逐步修复。

---

## 🐛 v23.17 — 扫描模式支持断点续传

### 新增
- 导入扫描记录后，扫描模式也自动跳过已完成的文件（之前仅处理模式支持）
- 失败的扫描在下次导入记录后**自动重试**（不在已完成列表里）

---

## 🐛 v23.16 — 记录编辑器（删除行 + 导入/导出断点续传）

### 新增
- **表格右键菜单**：选中行右键 → 删除选中行（运行中不可删除）
- **保存记录 v2**：升级为 JSON v2 格式，记录每行 `status` + `done` 标记
- **导入记录**：恢复历史状态 + 已完成绿色标记，处理时自动跳过
- 点击「保存记录」或「导入记录」按钮操作

### 问题
开启「调试模式」批量处理（如 73 个任务）时，C 盘被写满导致 `[Errno 28] No space left on device`。
根因：调试模式的本意是「保留当前任务中间产物供排查」，但旧实现写成了**所有任务产物永久保留、跨任务不清理**：

- `Worker._clean_temp_dir`：调试模式直接 `return`，整个 `tmp/temp/` 工作目录从不清理；
- `pipeline._detect_audio`：调试模式保留每片音频段 WAV；
- `subtitle_detect._ocr_with_tesseract`：调试模式保留每 PGS 字幕几百张 1080p 帧图；
- `ai_worker._ensure_local_copy`：UNC 整片复制在调试模式永不删除。

叠加后 73 个任务的全部中间产物堆积在本地，把 C 盘（118GB）打满。

### 修复（滑动窗口清理）
- **任务级滑动窗口**（`Worker._post_task_cleanup`）：
  - 成功 / 跳过任务：无论是否调试，立即清理 `tmp/temp/`；若此前保留过失败快照也一并回收。
  - 失败 / 异常 + 调试：把当前 `tmp/temp/` 整目录移栽到 `tmp/debug_last/` 作为快照，**先清旧快照，保证最多只留 1 个**。
  - 失败 / 异常 + 非调试：直接清理。
- **启动前**（`_purge_stale_temp_on_start`）：清掉 `tmp/temp/` 与 `tmp/debug_last/` 历史残留，避免上次运行遗留物继续占盘。
- **收尾 / 关闭**（`closeEvent`）：额外清理 `tmp/debug_last/`。
- **`ai_worker` UNC 整片复制加固**：改用源路径 hash 的稳定命名（同一文件不重复复制），并加 `_purge_stale_cache(max_keep=2)` 窗口清理，杜绝整部电影副本无限堆积。

### 效果
调试模式仍保留最近一个失败任务的产物（`tmp/debug_last/`）供排查，**但磁盘占用有硬上限**，不再随任务数线性增长；成功任务实时释放空间。

---

## 🎬 v23 正式版

### 🖼️ 托盘图标
- **手绘 SVG 图标集**：墨镜（待机）、放大镜（扫描扫描线动画）、齿轮（旋转动画）、绿色对勾（完成）
- **IPC 通信 → 文件轮询**：QLocalSocket/QLocalServer 改为 `tmp/tray_status.txt` 文件轮询，零额外依赖
- **完成状态保持 3 秒**：任务完成后显示绿色对勾+提示音，再回墨镜
- **通知用状态图标**：气泡通知现在显示当前状态图标而非旧箭靶

### 🔧 管道重构
- **提取+检测分离**：`analyze_file` 先集中提取所有音轨 WAV 段，再统一 AI 检测，仅一次 NAS 读取
- **字幕批量提取**：`subtitle_detect.extract_only()` + `detect_from_file()`，提取和 OCR 分离
- **`_run_ocr` 移除**：改为 `utils.ocr_image_with_rapid`，消除重复代码

### 🎯 PGS 字幕 OCR 修复
- `color=black:r=1`：color 源 1fps + `shortest=1`，每次尝试精确 30 帧，不漏帧不溢出

### 📊 流量统计
- **psutil 网卡监控**：任务结束时蓝色 `keep` 级别输出网络读取/写入量，兼做任务分隔线
- **run.bat 预装 psutil**（build_portable.bat 已有）

### ⚙️ 配置
- `verbose_tools` 默认开启（默认勾选详细日志）
- 应用版本号 v22 → v23

### 🐛 修复
- 🩹 `QTimer` / `QLocalSocket` import 路径错误导致启动崩溃
- 🩹 `tray_monitor.py` SVG 初始化代码重复导致闪退
- 🩹 `_analyze_audio` 提取失败未合并检测结果

### 🔤 OCR 引擎
- **🧹 Tesseract → RapidOCR**：基于 OpenVINO，速度快 5 倍，简繁识别准确，无 DLL 兼容问题。
- **🗑️ PaddleOCR（过渡）→ RapidOCR**：去掉 PaddlePaddle 重型框架（800MB+），依赖 ONNX Runtime/OpenVINO。

### 🔧 轨道解析
- **📦 MKVToolNix 升级至 v100**：`--language` 直接接受 IETF BCP 47 码（如 `cmn-Hans`），移除 `--language-ietf`。
- **⚡ 移除 ffprobe**：只用 `mkvmerge -J`，兼容性更好。

### 🌐 电影产地判断
- **🔄 豆瓣 → TMDB**：纯正则解析 HTML，无需 API Key。

### 📤 输出流程
- **🚀 直接写 NAS**：mkvmerge `-o` 直接指向 NAS 目标路径，省去本地缓存+搬运两步。
- **♻️ 全部保留也封装**：所有文件一律走 mkvmerge 写入规范标签/名称。

### ⚙️ 配置
- 📋 `_schema_version` 机制，版本升级自动重置配置。
- 🧹 设置界面移除 Tesseract 路径、PaddleOCR 设备选择。

### 📊 系统监控
- **💾 GPU → 磁盘 I/O**（读写双线彩色趋势图）。
- 🌐 网络拆分为上下行双线（橙/黄）。
- 📈 Sparkline 支持多数据叠加显示。
- 🎨 图标支持 emoji 渲染 + 自动降级。

### 🐛 修复
- 🩹 `settings_dialog.py` 硬编码默认值与 DEFAULTS 不一致问题。
- 🩹 `import datetime` 缺失导致「开始处理」崩溃。
- 🩹 `Track` 缺少 `detected_kind` 字段导致 `save_record` 崩溃。
- 🩹 `_ERR_CAP` / `_VERB_CAP` 常量丢失导致所有外部命令失败。
- 🩹 转封装进度实时输出到 GUI 日志。
- 🩹 输出文件验证后通知用户。
- 🩹 语言码映射补全至 105 条，未知码自动降级 `und`。

### 🗑️ 移除
- ❌ **Tesseract**（`tools/tesseract/` 目录可手动删除）
- ❌ **PaddlePaddle / PaddleOCR**（`pip uninstall paddlepaddle paddleocr paddlex`）
- ❌ **ffprobe** 探测路径
- ❌ **build_gpu.bat**（RapidOCR 无需 GPU 专用版）
- ❌ 多余空文件 `encodings`、`Lib`
