# MediaMetaFixer 变更说明

## 📌 v23.1 (README 优化)

### 📝 文档
- **README 顶部徽章栏**：仿 RapidOCR 风格，使用 `for-the-badge` 样式展示版本 / 许可 / AI 引擎 / 原生工具 / 运行环境
- **致谢章节**：新增「🙏 致谢与开源引用」表格，列出全部 AI 项目、原生工具、Python 库、开放数据源
- **第三方工具版本表**：明确 `build_portable.bat` 自动下载的工具版本（Python 3.11.9 / FFmpeg / MKVToolNix v100 / 7-Zip 9.20）

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
