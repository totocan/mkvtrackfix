# MediaMetaFixer 变更说明

## 🐛 v23.43 — 界面字体移至右侧 + 预缓存下拉 + 译文中文化

### UI 调整
- 界面字体设置从左侧移到右侧面板（在预缓存上方）
- 预缓存提前量改为下拉框 `[2, 3, 5, 10, 15, 20]`，默认 2
- 右侧字号缩小（标题 18pt / 副标题 11pt / 感谢语 12pt）
- ❤️ 红色显示修复（`setTextFormat(Qt.RichText)` 强制 HTML 渲染）
- 设置按钮「确定/取消」移至右侧赞赏区底部居中
- 设置窗口加宽至 1800×900，左右比例 6:4
- 全局 Qt 中文翻译加载（滚动条 Tooltip 等变中文）

### 新增
- 预缓存提前量可配置（设置右侧面板）
- OCR 每段超时下拉 `[45, 60, 75, 90]` 秒，默认 60（原写死 45）
- 「自动获取 OpenID」按钮（填完 AppID/AppSecret 后点击）

### 修复
- 缓存 MKV/MP4 文件头验证（magic bytes），拦截损坏缓存
- `_relocate_output` 不再覆盖 namer 生成的智能命名
- `cache.current_idx` 从未设置 → 预缓存实际从未工作

---

## 🐛 v23.42 — OCR 超时配置 + 预缓存配置 + ❤️ 红色

### 新增
- `ocr_attempt_timeout` 配置，每段 OCR 尝试超时下拉 `[45,60,75,90]`，默认 60
- `prefetch_ahead` 配置，预缓存提前量可设置（1~20，默认 2）
- `subtitle_extract_timeout` 默认从 180 改为 90 秒

### 修复
- 右侧赞赏区字体改用 `QFont`（CSS px 在 Qt 中不生效）
- ❤️ 改为红色（`color:#e53935`）

---

## 🐛 v23.41 — 右侧字体改用 QFont（CSS px 失效修复）

---

## 🐛 v23.40 — 按钮移至右侧赞赏区底部 + Qt 中文翻译加载

---

## 🐛 v23.39 — 设置窗口加宽+赞赏区字号放大

---

## 🐛 v23.38 — 设置左右比例 6:4 + 移除 embed_qr.py

---

## 🐛 v23.37 — 设置界面左右分栏 + 在线要饭赞赏码

---

## 🐛 v23.36 — 修复预缓存未工作（cache.current_idx 从未设置）

---

## 🐛 v23.35 — 缓存 MKV 头验证 + 自动获取 OpenID 按钮

---

## 🐛 v23.34 — 嵌入真实公众号二维码

---

## 🐛 v23.33 — 扫描后自动处理 + 微信推送（公众号客服消息）

---

### 新增
- 处理完成后，点「仅保留有问题的」自动清掉成功的文件，只留失败/异常/跳过的
- 流程闭环：扫描 → 处理 → 一键保留有问题的 → 调参数 → 重新扫描/处理

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
