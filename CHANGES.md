# MediaMetaFixer 变更说明

## 📌 v23.14 (配置兼容性修复 — 覆盖安装不再丢配置)

### 🛡️ 修复（配置兼容性，严重）
- **🐞 根因**：`config.py` 用应用版本号 `APP_VERSION`（如 `v23`）做配置兼容性判断，
  覆盖安装时新版本的 config.json 覆盖了旧配置（无论有无 `_schema_version`），
  `load()` 判定版本不匹配 → **清空用户全部自定义配置并重置**，导致反复「配置升级」日志、设置丢失
- **✅ schema 版本与应用版本解耦**：新增 `SCHEMA_VERSION="1"`，**仅它**决定配置兼容性；
  `APP_VERSION`（如 `v23.13`）仅用于界面/日志署名，改应用版本不再触发配置重置
- **✅ 升级改为合并而非清空**：`_schema_version` 不匹配时 `{**DEFAULTS, **用户旧值}` 合并，
  保留用户改过的字体 / 调试 / 重命名 / TMDB 等设置，只丢弃 DEFAULTS 外的未知 key
- **✅ 路径统一**：`resolve_config_path()` 改基于 `app_root()`（层级关系推导，不假设父目录名，
  无论 `mediameta_fixer` / `mkvtrackfix-main` / `mkvtrackfix v9` 都正确），删除误导的 `CONFIG_PATH` 常量
- **✅ save 显式写出 `_schema_version`**：覆盖安装后首次启动自动修好 config，不再反复升级
- **✅ 不硬编码父目录名**：全代码库确认无 `mediameta_fixer` / `mkvtrackfix-*` 等目录名假设，迁移/改名无损

---

## 📌 v23.13 (缓存丢失自愈 + 单任务失败不卡死)

### 🛡️ 修复（健壮性，严重）
- **🐞 卡死根因**：`开始处理` 阶段某任务（如 2160p UHD 大文件）缓存因 NAS 网络抖动失败/丢失，
  `ready[idx]` 标记缺失或指向已消失的 `tmp/N`，`wait_until_ready(idx)` 无限等待 → 整队卡在最后任务
- **✅ 缓存丢失自愈**：`wait_until_ready` 增加**物理校验**——`ready[idx]` 指向文件必须真实存在且非空，
  否则判定「缓存丢失」并主动 `_preload_one(idx)` **重新缓存一次**，让任务继续而非卡死
- **✅ 预取失败重试**：`_preload_one` 内部失败**自动重试最多 3 次**（网络抖动自愈），
  且只在 `local` 物理落盘成功后才写 `ready`，杜绝「标记就绪但文件残破」的中间态
- **✅ 超时兜底**：`wait_until_ready` 按源文件大小估算超时阈值（每 GB 120s，夹在 300s~3600s），
  超时返回 `None`，**Worker 跳过该单任务并继续后续**，不再 `break` 中断整队
- **✅ 单任务失败隔离**：原 `wait_until_ready` 返回 `None` 时 `break` 全队 → 改为 `continue` 跳过单个任务，
  73 任务里个别失败不影响其余（避免「动不动跳过几十个」的反面——卡死更糟）

### 🔧 改动点
- `_preload_one(self, idx, max_retry=3)`：重写，加大重试 + 完成落盘校验 + 半截文件清理
- `wait_until_ready(self, idx, timeout_per_gb=120, max_timeout=3600)`：物理校验 + 缓存丢失重缓存 + 超时
- `Worker.run()`：`wait_until_ready` 返回 `None` 时 `continue`（跳过单任务）替代 `break`（终止全队）
- 清理逻辑与 v23.12 保持一致（WINDOW=3 滑窗，处理 N 清到 N-2）；`_run` 预取窗口 `curr+2` 与扫描阶段对齐，未改

---

## 📌 v23.12 (缓存清理收紧 — 处理 N 必清到 N-2)

### 🧹 优化（缓存占用）
- **清理边界收紧一格**：`on_task_done(i)` 的滑窗清理阈值由 `current_idx - WINDOW` 改为 `current_idx - (WINDOW - 1)`
  - 效果：处理任务 N 完成时**立即清理到 N-2（含 N-2）**，仅保留 N-1、N 两个目录
  - 长列表（如 73 任务）场景下，本地缓存峰值从「保留 N-2/N-1/N + 预取2个 ≈ 6~7 部」降为「保留 N-1/N + 预取2个 ≈ 5~6 部」，少缓存约 1 部电影体积
- **安全性不变**：清理边界（≤ N-2）与预取目标（≥ N，即 curr+WINDOW-1=N+2）间隔 ≥ 4 个目录，正处理/正在预取的目录绝不被误删，竞态根除性质保持

### 🔧 改动点
- `on_task_done(self, idx)`：`cleanup_threshold = self._current_idx - self.WINDOW` → `- (self.WINDOW - 1)`
- 同步更新 `CacheManager` 类文档字符串与 `_run` 注释的清理语义描述

---

## 📌 v23.11 (缓存滑窗状态机 — 修复竞态误删)

### 🛡️ 修复（严重）
- **🐞 缓存竞态误删**：原 `MAX_WINDOW=4` 设计在 73 文件长列表中，后台预取线程已缓存任务 5 的 `tmp/5/`，而 Worker 的 `cleanup_after(4)` 无条件 `rmtree` 整个 `tmp/N/` 会把**正在预取的任务 5 目录误删**，导致任务 5 等待一个已被删的缓存卡死
- **✅ 改为 `current_idx` 权威 + 滑动窗口（WINDOW=3）状态机**：
  - 后台预取仅推进到 `curr + WINDOW - 1`（当前 + 2 向前），任务 `curr+3` 不再预取
  - 新增 `mark_processing(i)`：Worker 处理任务前加锁置 `current_idx`，后台线程据此滑动窗口
  - `on_task_done(i)` 替代 `cleanup_after(i)`：加锁将 `current_idx` 推进到 `i+1`，**仅清理 `idx < current_idx - WINDOW` 的窗口外目录**，窗口内/正在预取目录绝不被删
  - 清理与预取受同一把锁保护，竞态根除

### 🔧 重构
- `CacheManager.MAX_WINDOW=4` → `WINDOW=3`
- `_run()` 预取目标 `curr + MAX_WINDOW` → `curr + WINDOW - 1`，去掉启动期一次性预取 4 个的激进策略
- `cleanup_after(i)` → `on_task_done(i)`（滑窗清理，保留调试模式 `temp/` 局部清理语义）
- `Worker.run()` 在每个任务处理前调用 `mark_processing(i)`，完成后调用 `on_task_done(i)`

---

## 📌 v23.10 (TMDB 缓存隔离)

### 🐛 修复
- **`analyze_file` 设置 TMDB 信息前清旧值**：`core/pipeline.py` 在写入 `_tmdb_movie_info` 前先 `pop` 旧值，避免共享 config 对象上的残留值串到下一个文件

---

## 📌 v23.9 (修复 TMDB 缓存串文件)

### 🐛 修复
- **🐞 多文件命名混乱根因**：扫描阶段 `analyze_file` 把 `_tmdb_movie_info` 写入共享 `cfg` 对象，最后文件的 TMDB 信息覆盖前面所有文件，导致 4 个文件输出同名（如全变「马路天使」）
- **✅ 修复**：`Worker.run()` 处理循环中 `pop("_tmdb_movie_info")` 让 `smart_rename` 按文件名独立解析；整个流程结束再 `cfg.pop()` 清理，防止影响下次操作

---

## 📌 v23.8 (内测声明)

### 📝 文档
- **README 新增「⚠️ 内测声明」**：明确非破坏式输出原则、推荐测试目录先验证、不承担责任等条款
- **版本标识改为 `v23 · Active Development`**
- **状态徽章 `stable` → `beta`**（橙色 `#E8960C`）

---

## 📌 v23.7 (移除空目录创建)

### ♻️ 清理
- **移除 `main.py` 自动创建空 `tmp/temp/` 的逻辑**：各任务使用独立的 `tmp/N/temp/`，不再全局生成

---

## 📌 v23.6 (OCR 采样优化)

### 🎯 采样策略
- **OCR 间隔 30s → 300s**：`attempt_starts` 改为 `[300, 600, 900, 1200]`，覆盖前 20 分钟，避免多段尝试落入无字幕空窗期

---

## 📌 v23.5 (图像字幕 OCR 修复)

### 🖼️ 图像字幕渲染
- **恢复 `.sup` 文件作为 OCR 输入源**：`[1:s]` 绝对正确匹配字幕流，无全局索引错位问题
- **移除 `overlay=shortest=1`**：避免因 `.sup` 文件 PTS 范围短导致输出帧数不足（实测仅 1 帧）
- **输出 `-t 30`**：硬限制每次尝试渲染 30 帧，不依赖输入时长，防止无限循环

---

## 📌 v23.4 (调试模式 + 代码清理)

### 🐛 修复
- 🩹 **调试模式保留文件**：`CacheManager.cleanup_after()` / `cleanup_all()` 支持 `debug_mode` 参数，仅清理 `temp/` 子目录，保留缓存视频和中间帧
- 🩹 **`detect_from_file` 参数分离**：增加 `video_path` 和 `extracted_path`，视频路径与字幕文件路径不再混淆

---

## 📌 v23.3 (缓存系统重构)

### 🚀 新特性
- **📦 本地缓存系统重构（CacheManager）**：
  - UNC 路径自动判度（`\\192...` 走本地缓存，`C:/...` 直读），零配置
  - 预缓存窗口从 1 个扩大到 **4 个**（含当前任务）
  - 每个视频独立目录 `tmp/N/` + `tmp/N/temp/`，用完即焚
  - Worker 等待缓存就绪，输出等待时间（如「等待 36 秒」）
  - 处理完毕即时清理对应目录

### ⚡ 优化
- **⚡ 批量字幕提取 `extract_all()`**：单次 `mkvextract tracks` 提取全部字幕轨，只读一次源文件
- **⚡ remux 直写 NAS**：`remux()` 新增 `orig_path` 参数，输出路径直接指向 NAS 原目录，省去本地搬运
- **⚡ pipeline 参数化 `temp_dir`**：`analyze_file` / `process_file` / `process_tracks` 均支持外部传入临时目录

### 📊 性能
- **NAS 读取大幅降低**：实测 5.94GB 文件从约 **29.7GB → 6.17GB**（仅缓存一次 = 1× 读取 + 少量开销）

---

## 📌 v23.2 (本地徽章 + 仓库公开)

### 🎨 README 徽章
- **本地 SVG 徽章**：不再依赖 `shields.io`，将 20 个 `for-the-badge` 风格 SVG 生成并存放于 `assets/badges/`
- **按功能分组**：品牌 / AI 引擎 / 原生工具链 / 运行环境，共 4 行
- **全链路内嵌**：徽章图片通过相对路径引用，断网或国内外网络差异均可正常显示

### 🏠 仓库设置
- **仓库转为公开**：`totocan/mkvtrackfix` 已改为 Public，任何人可访问、Star、Fork

---

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
