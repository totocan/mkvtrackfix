# MediaMetaFixer 变更说明

## ✏️ v23.16 — 记录编辑器：删除行 + 断点续传

### 需求
扫描后发现个别文件不行、或处理中途失败，希望：
- 在表格上 **删除** 某一行（不行/不想处理的），而不是整批重来；
- 处理中断后，**保存记录 → 导入 → 继续处理剩下的**，已完成的不重复跑。

### 新增
- **表格右键菜单「删除选中行」**：同步从 `files` / `results` / `_track_data` / `_completed` 删除并重填表格；
  处理进行中自动禁用删除（先「停止当前」），避免行号错位。
- **`save_record` 升级为 v2**：除音轨/字幕动作外，额外记录每行的 `status`（状态列文字）、`done`（是否成功完成）。
- **`load_record` 断点续传**：导入时还原状态列，已完成的行标绿（#1b5e20）；
  若记录含 `done` 标记，开始处理时 Worker **自动跳过**这些文件，只处理剩余任务。
- **兼容 v1 老记录**（无 status/done 字段）正常导入，不自动跳过。

### 效果
完整闭环：扫描 → 右键删掉不行的 → 开始处理 → 中途失败先「停止」→
「保存记录」→ 关掉/重开 → 「导入记录」（已完成行自动标绿）→ 「开始处理」只跑剩下的。
也可手动右键删除已完成行再保存，行为一致。

---

## 🐛 v23.15 — 调试模式磁盘打满修复

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
