# 电影音轨 / 字幕标签批量修复工具（v23） mkvtrackfix
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
