<div align="center">

# 🎬 mkvtrackfix

### 电影音轨 / 字幕标签批量修复工具

**v23 正式版 · Powered by Faster-Whisper + RapidOCR-OpenVINO + MKVToolNix**

<br>

<!-- 主品牌徽章 -->
<a href="../../releases"><img src="https://img.shields.io/badge/版本-v23-FF6B35?style=for-the-badge&logo=git&logoColor=white" alt="Version"/></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/许可-GPL--3.0-blue?style=for-the-badge&logo=gnu&logoColor=white" alt="License"/></a>
<a href="#"><img src="https://img.shields.io/badge/SemVer-2.0.0-orange?style=for-the-badge&logo=semver&logoColor=white" alt="SemVer"/></a>
<a href="#"><img src="https://img.shields.io/badge/状态-stable-success?style=for-the-badge&logo=checkmarx&logoColor=white" alt="Status"/></a>
<a href="#"><img src="https://img.shields.io/badge/代码-black-000000?style=for-the-badge&logo=python&logoColor=white" alt="Code Style"/></a>

<br>

<!-- AI / ML 引擎（开源关联） -->
<a href="https://github.com/SYSTRAN/faster-whisper"><img src="https://img.shields.io/badge/faster--whisper-CTranslate2-FF6B35?style=for-the-badge&logo=openai&logoColor=white" alt="faster-whisper"/></a>
<a href="https://github.com/RapidAI/RapidOCR"><img src="https://img.shields.io/badge/RapidOCR-OpenVINO-0071C5?style=for-the-badge&logo=intel&logoColor=white" alt="RapidOCR"/></a>
<a href="https://www.modelscope.cn/models/gpustack/faster-whisper-medium"><img src="https://img.shields.io/badge/ModelScope-魔搭-6242D5?style=for-the-badge" alt="ModelScope"/></a>
<a href="https://huggingface.co/Systran/faster-whisper-medium"><img src="https://img.shields.io/badge/HuggingFace-🤗-FFD21E?style=for-the-badge&logo=huggingface&logoColor=black" alt="HuggingFace"/></a>

<br>

<!-- 原生工具链 -->
<a href="https://ffmpeg.org/"><img src="https://img.shields.io/badge/ffmpeg-音视频处理-007808?style=for-the-badge&logo=ffmpeg&logoColor=white" alt="ffmpeg"/></a>
<a href="https://mkvtoolnix.download/"><img src="https://img.shields.io/badge/MKVToolNix-v100-1A1A1A?style=for-the-badge" alt="MKVToolNix"/></a>
<a href="https://www.riverbankcomputing.com/software/pyqt/"><img src="https://img.shields.io/badge/PyQt5-GUI-41CD52?style=for-the-badge&logo=qt&logoColor=white" alt="PyQt5"/></a>
<a href="https://www.themoviedb.org/"><img src="https://img.shields.io/badge/TMDB-开放数据-01B4E4?style=for-the-badge&logo=themoviedatabase&logoColor=white" alt="TMDB"/></a>
<a href="https://github.com/giampaolo/psutil"><img src="https://img.shields.io/badge/psutil-系统监控-FFD43B?style=for-the-badge&logo=python&logoColor=blue" alt="psutil"/></a>

<br>

<!-- 运行环境 -->
<a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.11.9-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python"/></a>
<a href="#"><img src="https://img.shields.io/badge/平台-Windows%2010%2F11-0078D4?style=for-the-badge&logo=windows&logoColor=white" alt="Windows"/></a>
<a href="#"><img src="https://img.shields.io/badge/NAS-SMB%2FUNC-FFA500?style=for-the-badge" alt="NAS Support"/></a>
<a href="#"><img src="https://img.shields.io/badge/依赖-pip%20install-3776AB?style=for-the-badge&logo=pypi&logoColor=white" alt="PyPI"/></a>

<br>

[📥 下载便携版](../../releases) · [📝 更新日志](CHANGES.md) · [🐛 反馈问题](../../issues) · [⭐ Star](../../stargazers)

</div>

---

- 批量修复 mp4 / mkv 电影的音轨与字幕语言标签，并把 mp4 重新封装为 mkv。
- 支持 UNC 网络路径（NAS），带 GUI、AI 识别、非破坏式输出与干跑预览。

---

## 主要功能

- **音轨语言 AI 识别**：用 `faster-whisper` 听取每条音轨片段（默认 10 秒×3段），识别语言。
- **规范标签**：写入 IETF BCP 47 语言码（如 `cmn`/`cmn-Hans`/`eng`），轨道名按编码与声道命名。
- **音轨精简策略**：保留 英语 + 普通话；无普通话时保留 英语 / 粤语。
- **字幕修复**：文本字幕直接读取；`sup`/`PGS` 等图像字幕使用 **ffmpeg 抽帧 + RapidOCR（OpenVINO）**，
  自动区分 **简体 / 繁体 / 中英双语**，支持简繁正确识别。
- **统一转封装**：非破坏式输出（`<名>.fixed.mkv`），直接写 NAS 目标目录。

---

## 快速开始（绿色便携版）

1. 双击 **`build_portable.bat`**：自动下载便携 Python、安装依赖（含 RapidOCR）、下载 ffmpeg / mkvmerge。
2. 之后每次双击 **`run.bat`** 即可运行。

---

## 主要变更

### v23 正式版
- **托盘图标动画**：墨镜（待机）→ 放大镜（扫描）→ 齿轮（处理）→ 绿色对勾（完成）+ 提示音
- **提取+检测分离**：音轨一次性批量提取三段 WAV（仅一次 NAS），再统一 AI 检测
- **PGS 字幕优化**：`color=black:r=1` 解决 `shortest=1` 漏帧 + 无限制溢出问题
- **IPC 通信**：QLocalServer → 文件轮询（零依赖，托盘动画实时同步）
- **流量统计**：`psutil` 任务结束时蓝色输出网络读取/写入量
- **详细日志**：默认勾选「详细记录第三方工具输出」
- **SVG 手绘图标**：窗口场记板 / 系统托盘墨镜 / 放大镜 / 齿轮 / 绿色对勾

### v22 正式版
- **OCR 引擎**：Tesseract → RapidOCR（基于 OpenVINO/ONNX Runtime，速度快 5 倍，简繁识别准确）
- **MKVToolNix**：升级至 v100（支持 IETF BCP 47 语言标签直写）
- **电影产地判断**：豆瓣 → TMDB（themoviedb.org，无需 API Key）
- **系统监控**：CPU / 内存 / 网络(上下行) / 磁盘(读写) 实时趋势图
- **输出流程**：直接写 NAS 目标路径，省去本地缓存搬运
- **配置文件自动升级**：版本号变更时自动重置为新默认值

---

## 使用流程

1. 填写源路径（支持 UNC 网络路径），点「收集文件」
2. 点「扫描并预览」→ 查看每条轨道的识别结果和计划动作
3. 确认无误后点「开始处理」→ mkvmerge 转封装输出 `.fixed.mkv`

---

## 目录结构

```
mediameta_fixer/
├── main.py                 # 入口
├── run.bat                 # 日常启动
├── build_portable.bat      # 组装绿色便携包
├── requirements.txt
├── core/                   # 核心逻辑
│   ├── logger.py / config.py / utils.py
│   ├── probe.py / remux.py / pipeline.py
│   ├── audio_detect.py / ai_worker.py / ai_child.py
│   ├── subtitle_detect.py / policy.py / lang_map.py
│   └── sys_monitor.py
├── gui/                    # 图形界面
│   ├── main_window.py / settings_dialog.py / widgets.py
│   └── sys_widget.py
├── tools/                  # 原生工具（ffmpeg / mkvmerge）
├── models/                 # AI 模型
└── logs/                   # 运行日志
```

---

## 📖 语言码字典

工具内置 **完整 105 条语言码映射**，覆盖 Whisper 全部 99 种语言 + 内部码，确保任意 Faster-Whisper 识别结果都能合规写入 MKV。未知语种自动降级为 `und`（未定义语言）。

### 🎯 Whisper 原生音轨码（100 条）

| ISO 639-1 | ISO 639-2 | 中文名 | English |
|:---------:|:---------:|:------|:--------|
| af | afr | 南非荷兰语 | Afrikaans |
| am | amh | 阿姆哈拉语 | Amharic |
| ar | ara | 阿拉伯语 | Arabic |
| as | asm | 阿萨姆语 | Assamese |
| az | aze | 阿塞拜疆语 | Azerbaijani |
| ba | bak | 巴什基尔语 | Bashkir |
| be | bel | 白俄罗斯语 | Belarusian |
| bg | bul | 保加利亚语 | Bulgarian |
| bn | ben | 孟加拉语 | Bengali |
| bo | bod | 藏语 | Tibetan |
| br | bre | 布列塔尼语 | Breton |
| bs | bos | 波斯尼亚语 | Bosnian |
| ca | cat | 加泰罗尼亚语 | Catalan |
| cs | ces | 捷克语 | Czech |
| cy | cym | 威尔士语 | Welsh |
| da | dan | 丹麦语 | Danish |
| de | deu | 德语 | German |
| el | ell | 希腊语 | Greek |
| en | eng | 英语 | English |
| es | spa | 西班牙语 | Spanish |
| et | est | 爱沙尼亚语 | Estonian |
| eu | eus | 巴斯克语 | Basque |
| fa | fas | 波斯语 | Persian |
| fi | fin | 芬兰语 | Finnish |
| fo | fao | 法罗语 | Faroese |
| fr | fra | 法语 | French |
| gl | glg | 加利西亚语 | Galician |
| gu | guj | 古吉拉特语 | Gujarati |
| ha | hau | 豪萨语 | Hausa |
| haw | haw | 夏威夷语 | Hawaiian |
| he | heb | 希伯来语 | Hebrew |
| hi | hin | 印地语 | Hindi |
| hr | hrv | 克罗地亚语 | Croatian |
| ht | hat | 海地克里奥尔语 | Haitian Creole |
| hu | hun | 匈牙利语 | Hungarian |
| hy | hye | 亚美尼亚语 | Armenian |
| id | ind | 印尼语 | Indonesian |
| is | isl | 冰岛语 | Icelandic |
| it | ita | 意大利语 | Italian |
| ja | jpn | 日语 | Japanese |
| jw | jav | 爪哇语 | Javanese |
| ka | kat | 格鲁吉亚语 | Georgian |
| kk | kaz | 哈萨克语 | Kazakh |
| km | khm | 高棉语 | Khmer |
| kn | kan | 卡纳达语 | Kannada |
| ko | kor | 韩语 | Korean |
| la | lat | 拉丁语 | Latin |
| lb | ltz | 卢森堡语 | Luxembourgish |
| ln | lin | 林加拉语 | Lingala |
| lo | lao | 老挝语 | Lao |
| lt | lit | 立陶宛语 | Lithuanian |
| lv | lav | 拉脱维亚语 | Latvian |
| mg | mlg | 马达加斯加语 | Malagasy |
| mi | mri | 毛利语 | Maori |
| mk | mkd | 马其顿语 | Macedonian |
| ml | mal | 马拉雅拉姆语 | Malayalam |
| mn | mon | 蒙古语 | Mongolian |
| mr | mar | 马拉地语 | Marathi |
| ms | msa | 马来语 | Malay |
| mt | mlt | 马耳他语 | Maltese |
| my | mya | 缅甸语 | Myanmar (Burmese) |
| ne | nep | 尼泊尔语 | Nepali |
| nl | nld | 荷兰语 | Dutch |
| nn | nno | 挪威尼诺斯克语 | Norwegian Nynorsk |
| no | nor | 挪威语 | Norwegian |
| oc | oci | 奥克语 | Occitan |
| pa | pan | 旁遮普语 | Punjabi |
| pl | pol | 波兰语 | Polish |
| ps | pus | 普什图语 | Pashto |
| pt | por | 葡萄牙语 | Portuguese |
| ro | ron | 罗马尼亚语 | Romanian |
| ru | rus | 俄语 | Russian |
| sa | san | 梵语 | Sanskrit |
| sd | snd | 信德语 | Sindhi |
| si | sin | 僧伽罗语 | Sinhala |
| sk | slk | 斯洛伐克语 | Slovak |
| sl | slv | 斯洛文尼亚语 | Slovenian |
| sn | sna | 绍纳语 | Shona |
| so | som | 索马里语 | Somali |
| sq | sqi | 阿尔巴尼亚语 | Albanian |
| sr | srp | 塞尔维亚语 | Serbian |
| su | sun | 巽他语 | Sundanese |
| sv | swe | 瑞典语 | Swedish |
| sw | swa | 斯瓦希里语 | Swahili |
| ta | tam | 泰米尔语 | Tamil |
| te | tel | 泰卢固语 | Telugu |
| tg | tgk | 塔吉克语 | Tajik |
| th | tha | 泰语 | Thai |
| tk | tuk | 土库曼语 | Turkmen |
| tl | tgl | 他加禄语 | Tagalog |
| tr | tur | 土耳其语 | Turkish |
| tt | tat | 鞑靼语 | Tatar |
| uk | ukr | 乌克兰语 | Ukrainian |
| ur | urd | 乌尔都语 | Urdu |
| uz | uzb | 乌兹别克语 | Uzbek |
| vi | vie | 越南语 | Vietnamese |
| yi | yid | 意第绪语 | Yiddish |
| yo | yor | 约鲁巴语 | Yoruba |
| yue | yue | 粤语 | Cantonese |
| zh | cmn | 普通话 | Mandarin Chinese |

### 🏷️ 内部码（字幕/策略用，5 条）

| 码 | ISO 输出 | 说明 |
|:--:|:--------:|:----|
| cmn | cmn | 普通话（直接码） |
| chi | cmn | Matroska 传统中文码 |
| zho | cmn | ISO 639-2 变体 |
| cmn-hans | cmn-hans | 简体中文字幕（BCP 47） |
| cmn-hant | cmn-hant | 繁体中文字幕（BCP 47） |

> **🔒 兜底保护**：不在表中的任何语言码 → 自动降级为 `und`（未定义语言），保证 `mkvmerge` 绝不因语言码参数报错。

---

## 🙏 致谢与开源引用

mkvtrackfix 完全构建于开源社区的肩膀之上，**v23** 版本涉及的核心项目如下（按依赖层级排序）：

### 🤖 AI / 机器学习
| 项目 | 用途 | 链接 |
|:-----|:-----|:-----|
| **faster-whisper** | 音轨语种 AI 识别（CTranslate2 推理引擎，无需 PyTorch） | [github.com/SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) |
| **CTranslate2** | faster-whisper 底层推理框架 | [github.com/OpenNMT/CTranslate2](https://github.com/OpenNMT/CTranslate2) |
| **RapidOCR** | PGS 字幕 OCR 引擎 | [github.com/RapidAI/RapidOCR](https://github.com/RapidAI/RapidOCR) |
| **OpenVINO** | RapidOCR 推理后端（Intel CPU 加速） | [github.com/openvinotoolkit/openvino](https://github.com/openvinotoolkit/openvino) |
| **ONNX Runtime** | 跨平台模型推理 | [onnxruntime.ai](https://onnxruntime.ai/) |
| **ModelScope 魔搭** | 国内 faster-whisper 模型镜像源 | [modelscope.cn](https://www.modelscope.cn/) |
| **HuggingFace** | 国际 faster-whisper 模型镜像源 | [huggingface.co/Systran](https://huggingface.co/Systran) |

### 🛠️ 原生工具链
| 工具 | 用途 | 链接 |
|:-----|:-----|:-----|
| **FFmpeg** | 音轨/字幕提取、抽帧 | [ffmpeg.org](https://ffmpeg.org/) |
| **MKVToolNix v100** | MKV 转封装（IETF BCP 47 直写） | [mkvtoolnix.download](https://mkvtoolnix.download/) |
| **7-Zip (7za920)** | 解压 MKVToolNix 便携包 | [7-zip.org](https://www.7-zip.org/) |

### 🐍 Python 生态
| 库 | 版本 | 用途 |
|:---|:----:|:-----|
| **PyQt5** | ≥5.15 | 跨平台 GUI 框架 |
| **psutil** | ≥5.9 | 系统资源监控（CPU/内存/网络/磁盘） |
| **requests** | ≥2.31 | TMDB 查询、模型下载 |
| **langdetect** | ≥1.0.9 | 文本字幕语种辅助判定 |
| **modelscope** | 最新 | 国内模型下载（按需自动安装） |

### 🌐 开放数据
| 来源 | 用途 | 链接 |
|:-----|:-----|:-----|
| **TMDB** | 电影产地/语言查询（纯 HTML 解析，无需 API Key） | [themoviedb.org](https://www.themoviedb.org/) |

> 💡 **关于模型**：默认从 **ModelScope 魔搭社区** 下载（国内更快），失败自动回退到 **HuggingFace**。一次下载永久离线，存放在 `models/<size>/` 目录。

> 💡 **关于许可证**：本项目基于 **GPL-3.0** 开源；如需商用请遵守上游各依赖项目的许可证条款。

---

## 📦 第三方工具版本

构建脚本 `build_portable.bat` 会自动下载下列便携工具：

| 工具 | 发行版 | 下载源 |
|:-----|:-------|:-------|
| Python | 3.11.9 embed amd64 | python.org |
| FFmpeg | latest essentials | gyan.dev |
| MKVToolNix | 100.0 | mkvtoolnix.download |
| 7-Zip | 9.20 (7za) | 7-zip.org |

---

<div align="center">

如果这个项目对你有帮助，欢迎 ⭐ Star 支持一下！

<sub>Made with ❤️ by totocan · Powered by open source</sub>

</div>
