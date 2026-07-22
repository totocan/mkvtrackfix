# -*- coding: utf-8 -*-
"""
语言码注册表、简繁判定、音轨/字幕轨道名生成、und 启发式推断。

语言码遵循 mkvtoolnix / Matroska 规范：
  - 普通话  -> cmn   (ISO 639-3, mkvtoolnix 中 "Mandarin Chinese")
  - 粤语    -> yue   (ISO 639-3, mkvtoolnix 中 "Cantonese")
  - 英语    -> eng
  - 简体中文字幕 -> chi  (通用中文码, 播放器可识别)
  - 波兰/挪威/丹麦等 -> pol / nor / dan (ISO 639-2/B)

改进(v7)：
  - 音轨名称格式改为 "语言名 [编码 声道]"（如 "普通话 [AAC 2.0]"）
  - 混合表达：中文用中文名，外语用外语原语言名
  - 新增 und 启发式推断：根据文件名+轨道名+文件夹名推断语言
  - 字幕名称改进：混合表达
"""

import os
import re

# ---------------------------------------------------------------------------
# 语言总表：key = ISO 639-1 (whisper / langdetect 返回)，value = 规范信息
# iso  : 输出到 MKV 的语言码
# zh   : 中文显示名（混合表达中的中文名）
# en   : 英文显示名（混合表达中的外语名）
# ---------------------------------------------------------------------------
LANG_TABLE = {
    "af": {"iso": "afr", "zh": "南非荷兰语", "en": "Afrikaans"},
    "am": {"iso": "amh", "zh": "阿姆哈拉语", "en": "Amharic"},
    "ar": {"iso": "ara", "zh": "阿拉伯语", "en": "Arabic"},
    "as": {"iso": "asm", "zh": "阿萨姆语", "en": "Assamese"},
    "az": {"iso": "aze", "zh": "阿塞拜疆语", "en": "Azerbaijani"},
    "ba": {"iso": "bak", "zh": "巴什基尔语", "en": "Bashkir"},
    "be": {"iso": "bel", "zh": "白俄罗斯语", "en": "Belarusian"},
    "bg": {"iso": "bul", "zh": "保加利亚语", "en": "Bulgarian"},
    "bn": {"iso": "ben", "zh": "孟加拉语", "en": "Bengali"},
    "bo": {"iso": "bod", "zh": "藏语", "en": "Tibetan"},
    "br": {"iso": "bre", "zh": "布列塔尼语", "en": "Breton"},
    "bs": {"iso": "bos", "zh": "波斯尼亚语", "en": "Bosnian"},
    "ca": {"iso": "cat", "zh": "加泰罗尼亚语", "en": "Catalan"},
    "cs": {"iso": "ces", "zh": "捷克语", "en": "Czech"},
    "cy": {"iso": "cym", "zh": "威尔士语", "en": "Welsh"},
    "da": {"iso": "dan", "zh": "丹麦语", "en": "Danish"},
    "de": {"iso": "deu", "zh": "德语", "en": "German"},
    "el": {"iso": "ell", "zh": "希腊语", "en": "Greek"},
    "en": {"iso": "eng", "zh": "英语", "en": "English"},
    "es": {"iso": "spa", "zh": "西班牙语", "en": "Spanish"},
    "et": {"iso": "est", "zh": "爱沙尼亚语", "en": "Estonian"},
    "eu": {"iso": "eus", "zh": "巴斯克语", "en": "Basque"},
    "fa": {"iso": "fas", "zh": "波斯语", "en": "Persian"},
    "fi": {"iso": "fin", "zh": "芬兰语", "en": "Finnish"},
    "fo": {"iso": "fao", "zh": "法罗语", "en": "Faroese"},
    "fr": {"iso": "fra", "zh": "法语", "en": "French"},
    "gl": {"iso": "glg", "zh": "加利西亚语", "en": "Galician"},
    "gu": {"iso": "guj", "zh": "古吉拉特语", "en": "Gujarati"},
    "ha": {"iso": "hau", "zh": "豪萨语", "en": "Hausa"},
    "haw": {"iso": "haw", "zh": "夏威夷语", "en": "Hawaiian"},
    "he": {"iso": "heb", "zh": "希伯来语", "en": "Hebrew"},
    "hi": {"iso": "hin", "zh": "印地语", "en": "Hindi"},
    "hr": {"iso": "hrv", "zh": "克罗地亚语", "en": "Croatian"},
    "ht": {"iso": "hat", "zh": "海地克里奥尔语", "en": "Haitian Creole"},
    "hu": {"iso": "hun", "zh": "匈牙利语", "en": "Hungarian"},
    "hy": {"iso": "hye", "zh": "亚美尼亚语", "en": "Armenian"},
    "id": {"iso": "ind", "zh": "印尼语", "en": "Indonesian"},
    "is": {"iso": "isl", "zh": "冰岛语", "en": "Icelandic"},
    "it": {"iso": "ita", "zh": "意大利语", "en": "Italian"},
    "ja": {"iso": "jpn", "zh": "日语", "en": "Japanese"},
    "jw": {"iso": "jav", "zh": "爪哇语", "en": "Javanese"},
    "ka": {"iso": "kat", "zh": "格鲁吉亚语", "en": "Georgian"},
    "kk": {"iso": "kaz", "zh": "哈萨克语", "en": "Kazakh"},
    "km": {"iso": "khm", "zh": "高棉语", "en": "Khmer"},
    "kn": {"iso": "kan", "zh": "卡纳达语", "en": "Kannada"},
    "ko": {"iso": "kor", "zh": "韩语", "en": "Korean"},
    "la": {"iso": "lat", "zh": "拉丁语", "en": "Latin"},
    "lb": {"iso": "ltz", "zh": "卢森堡语", "en": "Luxembourgish"},
    "ln": {"iso": "lin", "zh": "林加拉语", "en": "Lingala"},
    "lo": {"iso": "lao", "zh": "老挝语", "en": "Lao"},
    "lt": {"iso": "lit", "zh": "立陶宛语", "en": "Lithuanian"},
    "lv": {"iso": "lav", "zh": "拉脱维亚语", "en": "Latvian"},
    "mg": {"iso": "mlg", "zh": "马达加斯加语", "en": "Malagasy"},
    "mi": {"iso": "mri", "zh": "毛利语", "en": "Maori"},
    "mk": {"iso": "mkd", "zh": "马其顿语", "en": "Macedonian"},
    "ml": {"iso": "mal", "zh": "马拉雅拉姆语", "en": "Malayalam"},
    "mn": {"iso": "mon", "zh": "蒙古语", "en": "Mongolian"},
    "mr": {"iso": "mar", "zh": "马拉地语", "en": "Marathi"},
    "ms": {"iso": "msa", "zh": "马来语", "en": "Malay"},
    "mt": {"iso": "mlt", "zh": "马耳他语", "en": "Maltese"},
    "my": {"iso": "mya", "zh": "缅甸语", "en": "Myanmar (Burmese)"},
    "ne": {"iso": "nep", "zh": "尼泊尔语", "en": "Nepali"},
    "nl": {"iso": "nld", "zh": "荷兰语", "en": "Dutch"},
    "nn": {"iso": "nno", "zh": "挪威尼诺斯克语", "en": "Norwegian Nynorsk"},
    "no": {"iso": "nor", "zh": "挪威语", "en": "Norwegian"},
    "oc": {"iso": "oci", "zh": "奥克语", "en": "Occitan"},
    "pa": {"iso": "pan", "zh": "旁遮普语", "en": "Punjabi"},
    "pl": {"iso": "pol", "zh": "波兰语", "en": "Polish"},
    "ps": {"iso": "pus", "zh": "普什图语", "en": "Pashto"},
    "pt": {"iso": "por", "zh": "葡萄牙语", "en": "Portuguese"},
    "ro": {"iso": "ron", "zh": "罗马尼亚语", "en": "Romanian"},
    "ru": {"iso": "rus", "zh": "俄语", "en": "Russian"},
    "sa": {"iso": "san", "zh": "梵语", "en": "Sanskrit"},
    "sd": {"iso": "snd", "zh": "信德语", "en": "Sindhi"},
    "si": {"iso": "sin", "zh": "僧伽罗语", "en": "Sinhala"},
    "sk": {"iso": "slk", "zh": "斯洛伐克语", "en": "Slovak"},
    "sl": {"iso": "slv", "zh": "斯洛文尼亚语", "en": "Slovenian"},
    "sn": {"iso": "sna", "zh": "绍纳语", "en": "Shona"},
    "so": {"iso": "som", "zh": "索马里语", "en": "Somali"},
    "sq": {"iso": "sqi", "zh": "阿尔巴尼亚语", "en": "Albanian"},
    "sr": {"iso": "srp", "zh": "塞尔维亚语", "en": "Serbian"},
    "su": {"iso": "sun", "zh": "巽他语", "en": "Sundanese"},
    "sv": {"iso": "swe", "zh": "瑞典语", "en": "Swedish"},
    "sw": {"iso": "swa", "zh": "斯瓦希里语", "en": "Swahili"},
    "ta": {"iso": "tam", "zh": "泰米尔语", "en": "Tamil"},
    "te": {"iso": "tel", "zh": "泰卢固语", "en": "Telugu"},
    "tg": {"iso": "tgk", "zh": "塔吉克语", "en": "Tajik"},
    "th": {"iso": "tha", "zh": "泰语", "en": "Thai"},
    "tk": {"iso": "tuk", "zh": "土库曼语", "en": "Turkmen"},
    "tl": {"iso": "tgl", "zh": "他加禄语", "en": "Tagalog"},
    "tr": {"iso": "tur", "zh": "土耳其语", "en": "Turkish"},
    "tt": {"iso": "tat", "zh": "鞑靼语", "en": "Tatar"},
    "uk": {"iso": "ukr", "zh": "乌克兰语", "en": "Ukrainian"},
    "ur": {"iso": "urd", "zh": "乌尔都语", "en": "Urdu"},
    "uz": {"iso": "uzb", "zh": "乌兹别克语", "en": "Uzbek"},
    "vi": {"iso": "vie", "zh": "越南语", "en": "Vietnamese"},
    "yi": {"iso": "yid", "zh": "意第绪语", "en": "Yiddish"},
    "yo": {"iso": "yor", "zh": "约鲁巴语", "en": "Yoruba"},
    "yue": {"iso": "yue", "zh": "粤语", "en": "Cantonese"},
    "zh": {"iso": "cmn", "zh": "普通话", "en": "Mandarin Chinese"},
    # 内部码（字幕检测/策略使用，非 Whisper 原生）
    "cmn": {"iso": "cmn", "zh": "普通话", "en": "Mandarin Chinese"},
    "chi": {"iso": "cmn", "zh": "普通话", "en": "Chinese"},
    "zho": {"iso": "cmn", "zh": "普通话", "en": "Chinese"},
    "cmn-hans": {"iso": "cmn-hans", "zh": "简体中文", "en": "Chinese (Simplified)"},
    "cmn-hant": {"iso": "cmn-hant", "zh": "繁体中文", "en": "Chinese (Traditional)"},
}
ANG_TABLE = {
    "en": {"iso": "eng", "zh": "英语", "en": "English"},
    "zh": {"iso": "cmn", "zh": "普通话", "en": "Mandarin Chinese"},
    "yue": {"iso": "yue", "zh": "粤语", "en": "Cantonese"},
    "cmn": {"iso": "cmn", "zh": "普通话", "en": "Mandarin Chinese"},
    "chi": {"iso": "cmn", "zh": "普通话", "en": "Mandarin Chinese"},
    "zho": {"iso": "cmn", "zh": "普通话", "en": "Chinese"},
    # BCP 47 繁简体字幕码（Language=chi + LanguageIETF=cmn-Hans/cmn-Hant）
    "cmn-hans": {"iso": "cmn-hans", "zh": "简体中文", "en": "Chinese (Simplified)"},
    "cmn-hant": {"iso": "cmn-hant", "zh": "繁体中文", "en": "Chinese (Traditional)"},
    "pl": {"iso": "pol", "zh": "波兰语", "en": "Polish"},
    "no": {"iso": "nor", "zh": "挪威语", "en": "Norwegian"},
    "da": {"iso": "dan", "zh": "丹麦语", "en": "Danish"},
    "sv": {"iso": "swe", "zh": "瑞典语", "en": "Swedish"},
    "fi": {"iso": "fin", "zh": "芬兰语", "en": "Finnish"},
    "is": {"iso": "ice", "zh": "冰岛语", "en": "Icelandic"},
    "fr": {"iso": "fre", "zh": "法语", "en": "French"},
    "de": {"iso": "ger", "zh": "德语", "en": "German"},
    "es": {"iso": "spa", "zh": "西班牙语", "en": "Spanish"},
    "it": {"iso": "ita", "zh": "意大利语", "en": "Italian"},
    "pt": {"iso": "por", "zh": "葡萄牙语", "en": "Portuguese"},
    "nl": {"iso": "dut", "zh": "荷兰语", "en": "Dutch"},
    "ru": {"iso": "rus", "zh": "俄语", "en": "Russian"},
    "uk": {"iso": "ukr", "zh": "乌克兰语", "en": "Ukrainian"},
    "ja": {"iso": "jpn", "zh": "日语", "en": "Japanese"},
    "jw": {"iso": "jav", "zh": "爪哇语", "en": "Javanese"},
    "ko": {"iso": "kor", "zh": "韩语", "en": "Korean"},
    "ar": {"iso": "ara", "zh": "阿拉伯语", "en": "Arabic"},
    "tr": {"iso": "tur", "zh": "土耳其语", "en": "Turkish"},
    "el": {"iso": "gre", "zh": "希腊语", "en": "Greek"},
    "he": {"iso": "heb", "zh": "希伯来语", "en": "Hebrew"},
    "fa": {"iso": "per", "zh": "波斯语", "en": "Persian"},
    "hi": {"iso": "hin", "zh": "印地语", "en": "Hindi"},
    "ur": {"iso": "urd", "zh": "乌尔都语", "en": "Urdu"},
    "bn": {"iso": "ben", "zh": "孟加拉语", "en": "Bengali"},
    "ta": {"iso": "tam", "zh": "泰米尔语", "en": "Tamil"},
    "te": {"iso": "tel", "zh": "泰卢固语", "en": "Telugu"},
    "th": {"iso": "tha", "zh": "泰语", "en": "Thai"},
    "vi": {"iso": "vie", "zh": "越南语", "en": "Vietnamese"},
    "id": {"iso": "ind", "zh": "印尼语", "en": "Indonesian"},
    "ms": {"iso": "may", "zh": "马来语", "en": "Malay"},
    "tl": {"iso": "fil", "zh": "菲律宾语", "en": "Filipino"},
    "fil": {"iso": "fil", "zh": "菲律宾语", "en": "Filipino"},
    "sw": {"iso": "swa", "zh": "斯瓦希里语", "en": "Swahili"},
    "hu": {"iso": "hun", "zh": "匈牙利语", "en": "Hungarian"},
    "cs": {"iso": "cze", "zh": "捷克语", "en": "Czech"},
    "sk": {"iso": "slo", "zh": "斯洛伐克语", "en": "Slovak"},
    "sl": {"iso": "slv", "zh": "斯洛文尼亚语", "en": "Slovenian"},
    "ro": {"iso": "rum", "zh": "罗马尼亚语", "en": "Romanian"},
    "bg": {"iso": "bul", "zh": "保加利亚语", "en": "Bulgarian"},
    "hr": {"iso": "hrv", "zh": "克罗地亚语", "en": "Croatian"},
    "sr": {"iso": "srp", "zh": "塞尔维亚语", "en": "Serbian"},
    "bs": {"iso": "bos", "zh": "波斯尼亚语", "en": "Bosnian"},
    "mk": {"iso": "mac", "zh": "马其顿语", "en": "Macedonian"},
    "sq": {"iso": "alb", "zh": "阿尔巴尼亚语", "en": "Albanian"},
    "et": {"iso": "est", "zh": "爱沙尼亚语", "en": "Estonian"},
    "lv": {"iso": "lav", "zh": "拉脱维亚语", "en": "Latvian"},
    "lt": {"iso": "lit", "zh": "立陶宛语", "en": "Lithuanian"},
    "ca": {"iso": "cat", "zh": "加泰罗尼亚语", "en": "Catalan"},
    "eu": {"iso": "baq", "zh": "巴斯克语", "en": "Basque"},
    "gl": {"iso": "glg", "zh": "加利西亚语", "en": "Galician"},
    "cy": {"iso": "wel", "zh": "威尔士语", "en": "Welsh"},
    "ga": {"iso": "gle", "zh": "爱尔兰语", "en": "Irish"},
    "mt": {"iso": "mlt", "zh": "马耳他语", "en": "Maltese"},
    "af": {"iso": "afr", "zh": "南非荷兰语", "en": "Afrikaans"},
    "ne": {"iso": "nep", "zh": "尼泊尔语", "en": "Nepali"},
    "pa": {"iso": "pan", "zh": "旁遮普语", "en": "Punjabi"},
    "km": {"iso": "khm", "zh": "高棉语", "en": "Khmer"},
    "lo": {"iso": "lao", "zh": "老挝语", "en": "Lao"},
    "my": {"iso": "bur", "zh": "缅甸语", "en": "Burmese"},
    "am": {"iso": "amh", "zh": "阿姆哈拉语", "en": "Amharic"},
    "ka": {"iso": "geo", "zh": "格鲁吉亚语", "en": "Georgian"},
    "hy": {"iso": "arm", "zh": "亚美尼亚语", "en": "Armenian"},
    "az": {"iso": "aze", "zh": "阿兹别克语", "en": "Azerbaijani"},
    "kk": {"iso": "kaz", "zh": "哈萨克语", "en": "Kazakh"},
    "uz": {"iso": "uzb", "zh": "乌兹别克语", "en": "Uzbek"},
    "mn": {"iso": "mon", "zh": "蒙古语", "en": "Mongolian"},
}

# ISO 639-2/3 码 → 语言信息的反向映射（用于从 mkvprobe 得到的 iso 码查找）
_ISO_TO_LANG = {}
for iso1, info in LANG_TABLE.items():
    iso = info["iso"]
    if iso not in _ISO_TO_LANG:
        _ISO_TO_LANG[iso] = info


def _unknown(iso1):
    """未知语种：iso1 大概率是 ISO 639-1（两字母），mkvmerge 不认识，
    统一降级为 und（未定义语言），避免 mkvmerge 参数报错。"""
    return {"iso": "und", "zh": iso1, "en": iso1}


# ---------------------------------------------------------------------------
# 简体 / 繁体判定
# ---------------------------------------------------------------------------
_TRAD2SIMP = {
    "麼": "么", "們": "们", "說": "说", "國": "国", "語": "语", "學": "学",
    "時": "时", "間": "间", "來": "来", "對": "对", "這": "这", "裡": "里",
    "後": "后", "開": "开", "關": "关", "電": "电", "東": "东", "車": "车",
    "長": "长", "門": "门", "馬": "马", "鳥": "鸟", "魚": "鱼", "龍": "龙",
    "雲": "云", "風": "风", "發": "发", "網": "网", "見": "见", "體": "体",
    "會": "会", "當": "当", "書": "书", "號": "号", "實": "实", "點": "点",
    "顯": "显", "與": "与", "從": "从", "個": "个", "話": "话", "覺": "觉",
    "親": "亲", "愛": "爱", "義": "义", "務": "务", "總": "总", "應": "应",
    "處": "处", "變": "变", "態": "态", "斷": "断", "舊": "旧", "歲": "岁",
    "歷": "历", "廠": "厂", "廣": "广", "飛": "飞", "飯": "饭", "飲": "饮",
    "館": "馆", "圖": "图", "團": "团", "圓": "圆", "場": "场", "陽": "阳",
    "陰": "阴", "際": "际", "銀": "银", "錢": "钱", "鐵": "铁", "鎮": "镇",
    "針": "针", "問": "问", "聞": "闻", "閱": "阅", "陳": "陈", "陣": "阵",
    "顏": "颜", "頁": "页", "頭": "头", "順": "顺", "須": "须", "題": "题",
    "養": "养", "鮮": "鲜", "烏": "乌", "無": "无", "備": "备", "複": "复",
    "費": "费", "試": "试", "詩": "诗", "詞": "词", "讀": "读", "誰": "谁",
    "請": "请", "認": "认", "讓": "让", "觀": "观", "視": "视", "講": "讲",
    "謝": "谢", "識": "识", "譯": "译", "遠": "远", "遺": "遗", "還": "还",
    "邊": "边", "過": "过", "進": "进", "運": "运", "週": "周", "師": "师",
    "歸": "归", "燈": "灯", "營": "营", "熱": "热", "爾": "尔", "獨": "独",
    "獻": "献", "產": "产", "畫": "画", "異": "异", "眾": "众", "確": "确",
    "礎": "础", "禮": "礼", "種": "种", "積": "积", "稱": "称", "穀": "谷",
    "窮": "穷", "筆": "笔", "紅": "红", "紙": "纸", "級": "级", "細": "细",
    "終": "终", "經": "经", "統": "统", "綠": "绿", "維": "维", "績": "绩",
    "續": "续", "線": "线", "習": "习", "職": "职", "聲": "声", "聯": "联",
    "興": "兴", "舉": "举", "質": "质", "賓": "宾", "賽": "赛", "贏": "赢",
    "軍": "军", "輕": "轻", "輸": "输", "轉": "转", "辭": "辞", "農": "农",
    "辦": "办", "達": "达", "遷": "迁", "選": "选", "醫": "医", "錯": "错",
    "鍊": "炼", "鏡": "镜", "陝": "陕", "險": "险", "隨": "随", "雙": "双",
    "驗": "验", "麗": "丽", "齊": "齐", "亂": "乱", "專": "专", "傳": "传",
    "價": "价", "億": "亿", "倫": "伦", "傷": "伤", "堅": "坚", "婦": "妇",
    "審": "审", "壽": "寿", "夢": "梦", "孫": "孙", "寶": "宝", "尋": "寻",
    "將": "将", "爭": "争", "狀": "状", "現": "现", "獎": "奖", "監": "监",
    "盤": "盘", "視": "视", "禪": "禅", "稅": "税", "絕": "绝", "網": "网",
    "義": "义", "聖": "圣", "腦": "脑", "與": "与", "葉": "叶", "萬": "万",
    "話": "话", "載": "载", "遊": "游", "運": "运", "過": "过", "達": "达",
    "遙": "遥", "鄉": "乡", "鋼": "钢", "錢": "钱", "錶": "表", "靈": "灵",
    "餘": "余", "騰": "腾", "灣": "湾", "讀": "读", "購": "购", "贈": "赠",
    "釋": "释", "裡": "里", "際": "际", "隱": "隐", "靜": "静", "頭": "头",
    "願": "愿", "類": "类", "點": "点",
}

_SIMP_CHARS = set(_TRAD2SIMP.values())
_TRAD_CHARS = set(_TRAD2SIMP.keys())

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def _count_cjk(text):
    return len(_CJK_RE.findall(text))


def _count_latin(text):
    return len(_LATIN_RE.findall(text))


def _max_consecutive_eng_words(text):
    """返回文本中连续英文字(word-like token)的最大长度，用于双语判定。"""
    tokens = re.findall(r"[A-Za-z']+", text)
    run = max_run = 0
    for t in tokens:
        if len(t) >= 2:
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0
    return max_run


def is_traditional(text):
    """返回 (是否为繁体, 繁体特征字数, 简体特征字数)。"""
    trad = 0
    simp = 0
    for ch in text:
        if ch in _TRAD_CHARS:
            trad += 1
        elif ch in _SIMP_CHARS:
            simp += 1
    return (trad > simp and trad > 0), trad, simp


def count_trad_chars(text):
    """统计文本中「繁体特征字」的数量（用于 OCR 繁简判定）。"""
    return sum(1 for ch in (text or "") if ch in _TRAD_CHARS)


# ---------------------------------------------------------------------------
# 语言信息获取
# ---------------------------------------------------------------------------
def lang_info(iso1, media_type="audio"):
    """由 ISO 639-1 代码得到规范语言信息。"""
    info = LANG_TABLE.get(iso1)
    if info is None:
        info = _unknown(iso1)
    out = dict(info)
    if iso1 in ("zh", "cmn") and media_type == "subtitle":
        out["iso"] = "chi"
        out["zh"] = "中文(简体)"
        out["en"] = "Chinese (Simplified)"
    return out


def lang_info_by_iso(iso, media_type="audio"):
    """由 ISO 639-2/3 输出码（如 eng/cmn/yue/pol）得到规范语言信息。"""
    # 直接查反向映射
    info = _ISO_TO_LANG.get(iso)
    if info:
        out = dict(info)
        if out.get("iso") in ("cmn", "chi") and media_type == "subtitle" \
                and iso not in ("cmn-hans", "cmn-hant"):
            out["iso"] = "chi"
            out["zh"] = "中文(简体)"
            out["en"] = "Chinese (Simplified)"
        return out
    return _unknown(iso)


def get_track_display_name(iso, track_type="audio"):
    """获取轨道显示名（混合表达）。

    规则：
      - 中文变体(cmn/yue) → 用中文名（普通话、粤语）
      - 英语(eng) → 用英文名（English）
      - 其他外语 → 用外语原语言名（French、Polish、Japanese...）
    """
    info = lang_info_by_iso(iso, media_type=track_type)
    if iso in ("cmn", "chi", "zho"):
        return info["zh"]  # 普通话 → 中文表达
    elif iso == "yue":
        return info["zh"]  # 粤语 → 中文表达
    elif iso == "eng":
        return info["en"]  # English → 英文表达
    else:
        # 其他外语 → 外语原语言名
        return info["en"]


# ---------------------------------------------------------------------------
# 字幕分类
# ---------------------------------------------------------------------------
def classify_subtitle_text(text, script_hint=None):
    """分析字幕纯文本，返回判定结果 dict。

    script_hint: 已废弃（v21.2 起不再使用），完全基于文本字符统计。

    返回 iso 字段说明：
      - "cmn-hans"   → 简体中文 / 简中英双语（Language=chi + IETF=cmn-Hans）
      - "cmn-hant"   → 繁体中文 / 繁中英双语（Language=chi + IETF=cmn-Hant）
    """
    cjk = _count_cjk(text)
    latin = _count_latin(text)
    has_cjk = cjk > 0
    has_latin = latin > 0

    if has_cjk:
        trad, t_count, s_count = is_traditional(text)
        total = t_count + s_count
        conf = (max(t_count, s_count) / total) if total else 0.0
        # v21.2: 繁体字 >= 2 即判为繁体（简体字幕中几乎不会出现繁体字）
        # 旧逻辑 trad > simp and trad > 0 对简短字幕过于严格
        is_trad = (t_count >= 2)
        # script_hint 已废弃，完全基于文本字符统计判断

        if is_trad:
            # 繁体 + 连续英文词 >= 5 且全长 >= 100 → 繁中英双语
            eng_run = _max_consecutive_eng_words(text)
            if len(text) >= 100 and eng_run >= 5:
                return {
                    "kind": "bilingual_traditional", "iso1": "cmn-hant",
                    "iso": "cmn-hant", "zh": "繁中英双语",
                    "en": "Chinese (Trad.) & English", "confidence": conf,
                }
            return {
                "kind": "chinese_traditional", "iso1": "cmn-hant",
                "iso": "cmn-hant", "zh": "繁体中文",
                "en": "Chinese (Traditional)", "confidence": conf,
            }
        # 简体 + 连续英文词 >= 5 且全长 >= 100 → 简中英双语
        eng_run = _max_consecutive_eng_words(text)
        if len(text) >= 100 and eng_run >= 5:
            return {
                "kind": "bilingual", "iso1": "cmn-hans",
                "iso": "cmn-hans", "zh": "简中英双语",
                "en": "Chinese & English", "confidence": conf,
            }
        return {
            "kind": "chinese_simplified", "iso1": "cmn-hans",
            "iso": "cmn-hans", "zh": "简体中文",
            "en": "Chinese (Simplified)", "confidence": conf,
        }

    from langdetect import detect_langs, DetectorFactory
    DetectorFactory.seed = 0
    try:
        langs = detect_langs(text)
        best = langs[0]
        iso1 = best.lang
        info = lang_info(iso1, media_type="subtitle")
        info["kind"] = "other" if iso1 != "en" else "english"
        info["confidence"] = float(best.prob)
        return info
    except Exception:
        return {"kind": "unknown", "iso1": "und", "iso": "und",
                "zh": "未知", "en": "Unknown", "confidence": 0.0}


def get_subtitle_display_name(kind, iso):
    """获取字幕轨道显示名（混合表达）。

    规则：
      - 简体中文 → "简体中文"
      - 简中英双语 → "简中英双语"
      - 繁体中文 → "繁体中文"
      - 繁中英双语 → "繁中英双语"
      - 纯英文 → "English"
      - 其他外语 → 外语原语言名（French、Polish...）
    """
    if kind == "chinese_simplified":
        return "简体中文"
    elif kind == "bilingual":
        return "简中英双语"
    elif kind == "chinese_traditional":
        return "繁体中文"
    elif kind == "bilingual_traditional":
        return "繁中英双语"
    elif kind == "english":
        return "English"
    elif kind == "unknown":
        return "未知"
    else:
        info = lang_info_by_iso(iso, media_type="subtitle")
        return info.get("en", iso)


# ---------------------------------------------------------------------------
# 音轨轨道名生成（v7 格式：语言名 [编码 声道]）
#   —— v23.54 起统一迁移到 core/naming.py（依赖本模块 + core/codec.py），
#      编码显示/简写/声道/Atmos 全部以 codec.py 为单一来源，
#      避免 lang_map 内部 _CODEC_DISPLAY 与 namer 的 _AUDIO_CODEC_SIMPLE
#      键格式不一致导致文件名简写失效。
#      make_audio_track_name / get_subtitle_track_name 现在由 naming.py 提供。
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# und 启发式推断：根据文件名+轨道名+文件夹名推断语言
# ---------------------------------------------------------------------------
# 轨道名中的语言关键词映射
_TRACK_NAME_LANG_HINTS = {
    # 中文
    "普通话": "cmn", "国语": "cmn", "中文": "cmn", "汉语": "cmn",
    "mandarin": "cmn", "chinese": "cmn",
    # 粤语
    "粤语": "yue", "广东话": "yue", "cantonese": "yue",
    # 英语
    "英语": "eng", "英文": "eng", "english": "eng",
    # 日语
    "日语": "jpn", "japanese": "jpn",
    # 韩语
    "韩语": "kor", "korean": "kor",
    # 法语
    "法语": "fre", "french": "fre",
    # 德语
    "德语": "ger", "german": "ger",
    # 俄语
    "俄语": "rus", "russian": "rus",
    # 西班牙语
    "西班牙语": "spa", "spanish": "spa",
}

# 文件名/文件夹名中的语言关键词
_FILENAME_LANG_HINTS = {
    # 中国电影相关关键词
    "国产": "cmn", "大陆": "cmn", "内地": "cmn",
    "香港": "yue", "港": "yue", "HK": "yue",
    "台湾": "cmn",  # 台湾电影大多普通话
    # 发行组名称中的语言提示
    "VFQ": "fre",   # 法国版
    "VFF": "fre",   # 法国版
    "MULTi": None,   # 多语言，不推断
}

_CJK_RE_PATH = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def heuristic_infer_language(track, file_path):
    """启发式推断 und 标签的语言。

    优先级：
      1. 轨道名(title)中的语言关键词
      2. 文件路径含中文 → 推断国产 → und 大概率是 cmn
      3. 文件名中的语言标记（如 VFQ → 法语）
      4. 如果都无法推断 → 保持 und

    返回 (iso_code, display_name, confidence, source)
    """
    # Step 1: 轨道名关键词
    title = (track.title or "").lower()
    if title:
        for keyword, iso in _TRACK_NAME_LANG_HINTS.items():
            if keyword.lower() in title:
                info = lang_info_by_iso(iso, media_type="audio")
                return (iso, info.get("zh", iso), 0.7, "track_name_hint")

    # Step 2: 文件路径含中文 → 推断国产 → und 大概率普通话
    full_path = file_path.replace("\\", "/")
    if _CJK_RE_PATH.search(full_path):
        # 含中文路径中的粤语关键词 → yue
        for kw in ("粤语", "粤", "Cantonese", "港版", "港片"):
            if kw in full_path:
                return ("yue", "粤语", 0.5, "path_chinese_hint")
        return ("cmn", "普通话", 0.5, "path_chinese_hint")

    # Step 3: 文件名中的语言标记
    basename = os.path.basename(file_path)
    for keyword, iso in _FILENAME_LANG_HINTS.items():
        if iso and keyword in basename:
            info = lang_info_by_iso(iso, media_type="audio")
            return (iso, info.get("zh", iso), 0.4, "filename_hint")

    # Step 4: 无法推断
    return ("und", "未知", 0.0, "no_hint")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def coerce_lang_code(raw):
    """把 ffprobe 得到的原始 language tag 规整为小写 ISO 639-1/3 或 BCP 47。"""
    if not raw:
        return "und"
    r = str(raw).strip().lower()
    if r.startswith("zh"):
        if "hant" in r or "tw" in r or "hk" in r:
            return "zh-trad"
        return "zh"
    # 保留 BCP 47 脚本子标签（如 cmn-Hans → cmn-hans）
    if r in ("cmn-hans", "cmn-hant"):
        return r
    return r.split("-")[0]


def is_chinese_code(code):
    return code in ("zh", "cmn", "yue", "chi", "zh-trad", "cmn-hans", "cmn-hant")


def normalize_iso_to_cmn(iso):
    """归一化中文变体码到 cmn（chi/zho/zh/cmn-hans/cmn-hant → cmn）。"""
    if iso in ("chi", "zho", "zh", "cmn-hans", "cmn-hant"):
        return "cmn"
    return iso


if __name__ == "__main__":
    # 语言系统自测（音轨/字幕轨道名生成已迁移到 naming.py）
    print("=== 语言信息 ===")
    print(lang_info("fr")["zh"])
    print(lang_info_by_iso("cmn")["zh"])
    print("=== 字幕分类 ===")
    print(classify_subtitle_text("这是简体中文，这是English test sentence here")["zh"])
    print("=== und 启发式 ===")
    from probe import Track
    t = Track(stream_index=1, track_id=1, track_type="audio", codec="aac",
              language_raw="und", language_norm="und", channels=2,
              channel_layout="stereo", title="国语")
    print(heuristic_infer_language(t, "C:/NAS/电影/东北警察故事3.mkv"))
