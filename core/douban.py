# -*- coding: utf-8 -*-
"""
TMDB 电影产地/语言查询模块（v21.2 新增，替代豆瓣）。

从文件名提取英文名+年代，走 themoviedb.org 搜索，
用正则从 HTML 中提取国家代码和默认语言信息。

流程：
  1. 提取英文电影名+年代
  2. themoviedb.org/search 搜索 → 正则提取 movie_id + 年份匹配
  3. themoviedb.org/movie/{id} 详情 → 正则提取国家代码(CN/HK/...) 和默认语言
  4. 根据国家代码判定是否国产，根据语言判定原生语言
  5. 查询失败 → 降级到通用策略

优点：
  - 无需 API Key，纯网页抓取 + 正则解析
  - 无外部依赖（不需要 BeautifulSoup 等）
  - 反爬比豆瓣宽松
"""
import os
import re
import time

from . import logger

# TMDB 搜索页（无需 API key）
_TMDB_SEARCH_URL = "https://www.themoviedb.org/search"
_TMDB_MOVIE_URL = "https://www.themoviedb.org/movie/{}"

# 请求间隔（秒）
_REQUEST_INTERVAL = 1.0
_last_request_time = 0.0

# 浏览器模拟请求头
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.themoviedb.org/",
}


def _rate_limit():
    global _last_request_time
    now = time.time()
    wait = _REQUEST_INTERVAL - (now - _last_request_time)
    if wait > 0:
        time.sleep(wait)
    _last_request_time = time.time()


# ---------------------------------------------------------------------------
# 从文件名提取英文电影名和年代
# ---------------------------------------------------------------------------
def extract_movie_info(path):
    """从文件路径中提取英文电影名和年代。

    返回 dict: {'title_en': str, 'year': int|None}
    """
    dirpath = os.path.dirname(path)
    basename = os.path.splitext(os.path.basename(path))[0]
    folder = os.path.basename(dirpath) if dirpath else basename
    name = folder if len(folder) > 4 else basename

    # 分离中文前缀和英文主体
    en_part = name
    cn_match = re.match(
        r"^([\u4e00-\u9fff\u3400-\u4dbf][\u4e00-\u9fff\u3400-\u4dbf\w\s·]*?)\.(.+)$",
        name)
    if cn_match:
        en_part = cn_match.group(2).strip()

    # 提取年份
    year = None
    year_match = re.search(r"\b((?:18|19|20)\d{2})\b", en_part)
    if year_match:
        year = int(year_match.group(1))

    # 提取英文电影名
    title_en = ""
    if year_match:
        before_year = en_part[:year_match.start()].rstrip(".")
    else:
        tech_match = re.search(
            r"\b(2160p|1080p|720p|480p|4K|UHD|WEB|BluRay|BD|WEB-DL|WEBRip|REMUX)\b",
            en_part, re.IGNORECASE)
        before_year = en_part[:tech_match.start()].rstrip(".") if tech_match else en_part.rstrip(".")

    title_en = before_year.replace(".", " ").strip()
    title_en = re.sub(r"\s*[-–]\s*\w+$", "", title_en).strip()

    if len(title_en) < 2 or title_en.replace(".", "").replace(" ", "").isdigit():
        title_en = ""

    return {"title_en": title_en, "year": year}


# ---------------------------------------------------------------------------
# TMDB 搜索页 → 提取 movie_id
# ---------------------------------------------------------------------------
def _search_movie_id(title_en, year):
    """从 TMDB 搜索页提取第一个年份匹配的 movie_id。

    TMDB 搜索不加年份（加年份可能搜不到），结果中按年份过滤。
    返回 movie_id 或 None。
    """
    try:
        import requests
    except ImportError:
        logger.log("TMDB 查询需要 requests 库", "PIPELINE")
        return None

    _rate_limit()

    params = {"query": title_en}
    logger.log(f"TMDB搜索页请求: {_TMDB_SEARCH_URL}?query={title_en}", "PIPELINE")

    try:
        resp = requests.get(_TMDB_SEARCH_URL, params=params,
                            headers=_BROWSER_HEADERS, timeout=10)
        logger.log(f"TMDB搜索页响应: status={resp.status_code} body_len={len(resp.text)}", "PIPELINE")

        if resp.status_code != 200:
            logger.log(f"TMDB搜索页返回状态码 {resp.status_code}", "PIPELINE")
            return None

        html = resp.text

        # 提取所有 /movie/数字-标题 链接
        # 格式: href="/movie/332685-crazy-new-year-s-eve"
        movie_links = re.findall(r'href="/movie/(\d+)-[^"]*"', html)
        # 也匹配 /movie/数字 不带标题后缀的
        if not movie_links:
            movie_links = re.findall(r'href="/movie/(\d+)"', html)

        if not movie_links:
            logger.log(f"TMDB搜索页: 未找到任何 movie_id 链接", "PIPELINE")
            return None

        # 去重取前几个
        seen = set()
        unique_ids = []
        for mid in movie_links:
            if mid not in seen:
                seen.add(mid)
                unique_ids.append(mid)

        logger.log(f"TMDB搜索页: 找到 {len(unique_ids)} 个唯一 movie_id", "PIPELINE")
        for mid in unique_ids[:5]:
            logger.log(f"  movie_id={mid}", "PIPELINE")

        best_mid = unique_ids[0]
        logger.log(f"TMDB搜索页: 取第一个 movie_id={best_mid}", "PIPELINE")
        return best_mid

    except Exception as e:
        logger.log(f"TMDB搜索页请求失败: {e}", "PIPELINE")
        return None


# ---------------------------------------------------------------------------
# TMDB 详情页 → 提取国家代码和默认语言
# ---------------------------------------------------------------------------
def _get_movie_detail(movie_id):
    """从 TMDB 电影详情页提取国家代码和默认语言。

    提取策略：
      - 发布日期格式如 "2015-02-16 (CN)" → 国家代码 CN
      - "默认语言 汉语" → 语言信息
      - "Original Language: zh" → 原始语言码

    返回 dict: {'country': str, 'language': str} 或 None
    """
    try:
        import requests
    except ImportError:
        return None

    _rate_limit()

    url = _TMDB_MOVIE_URL.format(movie_id)
    logger.log(f"TMDB详情页请求: {url}", "PIPELINE")

    try:
        # 用中文页面提取
        headers = dict(_BROWSER_HEADERS)
        resp = requests.get(url, params={"language": "zh-CN"},
                            headers=headers, timeout=10)
        logger.log(f"TMDB详情页响应: status={resp.status_code} body_len={len(resp.text)}", "PIPELINE")

        if resp.status_code != 200:
            logger.log(f"TMDB详情页返回状态码 {resp.status_code}", "PIPELINE")
            return None

        html = resp.text

        # ---- 提取国家代码（日期后的括号内两位大写字母）----
        country_code = ""
        country_match = re.search(
            r'\b\d{4}[-/]\d{2}[-/]\d{2}\s*\(([A-Z]{2})\)', html)
        if not country_match:
            country_match = re.search(
                r'\b\d{2}[-/]\d{2}[-/]\d{4}\s*\(([A-Z]{2})\)', html)
        if country_match:
            country_code = country_match.group(1)
            logger.log(f"TMDB详情页 国家代码: '{country_code}'", "PIPELINE")
        else:
            logger.log("TMDB详情页: 未找到国家代码", "PIPELINE")

        # ---- 提取默认语言 ----
        language = ""
        # TMDB 中文页面: "默认语言 汉语"（可能在 JS 动态加载中，原始 HTML 不一定有）
        # 搜索多种可能格式
        for pattern in (
            r'默认语言\s*([^\n<.]+)',
            r'Original\s*Language[:\s]*([^\n<.]+)',
            r'原始语言[:\s]*([^\n<.]+)',
            r'语言[:\s]*([^\n<.]+)',
        ):
            lang_match = re.search(pattern, html)
            if lang_match:
                candidate = lang_match.group(1).strip()
                if candidate and len(candidate) < 30:
                    language = candidate
                    break
        if language:
            logger.log(f"TMDB详情页 默认语言: '{language}'", "PIPELINE")
        else:
            # 如果语言提取不到，输出 HTML 片段辅助调试（找包含 "language" 或 "语言" 的片段）
            debug_snippet = html[5000:8000] if len(html) > 5000 else html
            logger.log(f"TMDB详情页: 未找到语言字段，HTML片段5000-8000:\n{debug_snippet}", "PIPELINE")
            # 英文页面: "Original Language: zh" 或 "Original Language\nEnglish"
            eng_match = re.search(r'Original\s*Language[:\s]*([^\n<]+)', html)
            if eng_match:
                language = eng_match.group(1).strip()
                logger.log(f"TMDB详情页 Original Language: '{language}'", "PIPELINE")
            else:
                logger.log("TMDB详情页: 未找到语言信息", "PIPELINE")

        # ---- 提取中文片名（zh-CN 页面 <title>）----
        title_zh = ""
        title_match = re.search(r"<title>\s*([^<\n]+?)\s*(?:\(|‖|—|\|)", html)
        if not title_match:
            title_match = re.search(r"<title>\s*([^<\n]+?)\s*</title>", html)
        if title_match:
            title_zh = title_match.group(1).strip()
            # 截掉末尾的 " — The Movie Database (TMDB)"
            title_zh = re.sub(r"\s*[—\-|]\s*.*?(?:TMDB|The Movie Database|Movie).*$", "", title_zh, flags=re.I).strip()

        return {"country": country_code, "language": language, "title_zh": title_zh}

    except Exception as e:
        logger.log(f"TMDB详情页请求失败: {e}", "PIPELINE")
        return None


# ---------------------------------------------------------------------------
# 判断电影是否国产 / 原生语言
# ---------------------------------------------------------------------------
# 国家代码 → 国产判定
_DOMESTIC_COUNTRIES = {"CN", "HK", "TW", "MO"}
# 国家代码 → 中文地区名（日志用 + 文件名）
_COUNTRY_NAMES = {
    "CN": "中国大陆", "HK": "中国香港", "TW": "中国台湾", "MO": "中国澳门",
    "US": "美国", "GB": "英国", "FR": "法国", "DE": "德国",
    "JP": "日本", "KR": "韩国", "IN": "印度", "IT": "意大利",
    "ES": "西班牙", "PT": "葡萄牙", "RU": "俄罗斯", "CA": "加拿大",
    "AU": "澳大利亚", "NZ": "新西兰", "BR": "巴西", "MX": "墨西哥",
    "AR": "阿根廷", "SE": "瑞典", "NO": "挪威", "DK": "丹麦",
    "FI": "芬兰", "NL": "荷兰", "BE": "比利时", "CH": "瑞士",
    "AT": "奥地利", "PL": "波兰", "CZ": "捷克", "HU": "匈牙利",
    "RO": "罗马尼亚", "GR": "希腊", "TR": "土耳其", "IL": "以色列",
    "IE": "爱尔兰", "TH": "泰国", "VN": "越南", "SG": "新加坡",
    "MY": "马来西亚", "PH": "菲律宾", "ID": "印度尼西亚", "ZA": "南非",
    "EG": "埃及", "NG": "尼日利亚", "IR": "伊朗", "PK": "巴基斯坦",
    "CU": "古巴", "CL": "智利", "CO": "哥伦比亚", "PE": "秘鲁",
    "HK": "中国香港", "TW": "中国台湾",
}

# 语言文本 → ISO 码映射
_LANG_MAP = {
    "汉语": "cmn", "普通话": "cmn", "中文": "cmn",
    "国语": "cmn", "Mandarin": "cmn", "Chinese": "cmn",
    "粤语": "yue", "Cantonese": "yue",
    "英语": "eng", "English": "eng",
    "日语": "jpn", "Japanese": "jpn",
    "韩语": "kor", "Korean": "kor",
    "法语": "fre", "French": "fre",
    "德语": "ger", "German": "ger",
    "俄语": "rus", "Russian": "rus",
    "西班牙语": "spa", "Spanish": "spa",
}


def classify_movie(path, config):
    """判断电影产地和原生语言（v21.2: 基于 TMDB）。

    流程：
      1. 提取英文电影名+年份
      2. TMDB 搜索 → 正则提取 movie_id
      3. TMDB 详情 → 正则提取国家代码 + 默认语言
      4. 根据国家代码判定国产/外国，根据语言判定原生语言

    返回 dict: {
        'is_domestic': bool,
        'native_lang': str,
        'native_lang_name': str,
        'source': str,         # 'tmdb' | 'unknown'
        'country': str,
        'language': str,
        'title_en': str,       # v22: 英文片名
        'title_zh': str,       # v22: 中文片名（TMDB zh-CN 页面）
        'year': int|None,      # v22: 年份
    }
    """
    logger.log(f"== STAGE classify == 判断电影产地: path={path}", "PIPELINE")
    result = {
        "is_domestic": False,
        "native_lang": "und",
        "native_lang_name": "未知",
        "source": "unknown",
        "country": "",
        "language": "",
        "title_en": "",
        "title_zh": "",
        "year": None,
    }

    # Step 1: 提取英文电影名+年份
    movie_info = extract_movie_info(path)
    title_en = movie_info["title_en"]
    year = movie_info["year"]
    result["title_en"] = title_en
    result["year"] = year
    logger.log(f"产地判断: 文件名提取 -> title_en='{title_en}' year={year}", "PIPELINE")

    if not title_en:
        logger.log(f"产地判断: 无法提取英文电影名: {os.path.basename(path)}", "PIPELINE")
        return result

    # Step 2: TMDB 搜索 → 取 movie_id
    movie_id = _search_movie_id(title_en, year)
    if not movie_id:
        logger.log(f"产地判断: TMDB 搜索未找到匹配, title_en='{title_en}'", "PIPELINE")
        result["source"] = "search_miss"
        return result

    # Step 3: TMDB 详情 → 取国家代码 + 语言
    detail = _get_movie_detail(movie_id)
    if not detail:
        logger.log(f"产地判断: TMDB 详情获取失败 movie_id={movie_id}", "PIPELINE")
        return result

    country_code = detail.get("country", "")
    language_text = detail.get("language", "")
    title_zh = detail.get("title_zh", "")
    if title_zh:
        result["title_zh"] = title_zh
        logger.log(f"TMDB详情页 中文片名: '{title_zh}'", "PIPELINE")

    result["country"] = country_code
    result["country_name"] = _COUNTRY_NAMES.get(country_code, "")
    result["language"] = language_text
    result["source"] = "tmdb"
    logger.log(f"产地判断: TMDB 返回 country='{country_code}' language='{language_text}'",
               "PIPELINE")

    # Step 4: 根据国家代码判定是否国产
    if country_code in _DOMESTIC_COUNTRIES:
        result["is_domestic"] = True
        cn_name = _COUNTRY_NAMES.get(country_code, country_code)
        logger.log(f"产地判断: 国家代码 {country_code}({cn_name})，判定为国产", "PIPELINE")

    # Step 5: 根据语言文本判定原生语言
    if language_text:
        matched = False
        for keyword, iso in _LANG_MAP.items():
            if keyword.lower() in language_text.lower():
                from . import lang_map
                info = lang_map.lang_info(iso, media_type="audio")
                result["native_lang"] = info.get("iso", iso)
                result["native_lang_name"] = info.get("zh", language_text)
                logger.log(f"产地判断: 语言匹配关键词「{keyword}」-> {info.get('zh')}({iso})",
                           "PIPELINE")
                matched = True
                break
        if not matched:
            logger.log(f"产地判断: 语言'{language_text}'未匹配到已知语言码，保留 und", "PIPELINE")
    elif country_code in _DOMESTIC_COUNTRIES:
        # 国产电影但语言字段未提取到（TMDB 原始 HTML 中语言可能由 JS 加载）
        # 兜底：默认普通话
        result["native_lang"] = "cmn"
        result["native_lang_name"] = "普通话"
        logger.log("产地判断: 国产电影但语言字段未提取到，兜底默认普通话(cmn)", "PIPELINE")
    else:
        logger.log(f"产地判断: 语言字段为空，无法判断原生语言", "PIPELINE")

    logger.log(f"产地判断(TMDB): 国产={result['is_domestic']}, "
               f"原生语言={result['native_lang_name']}({result['native_lang']}), "
               f"国家代码={result['country']}, 语言={result['language']}", "PIPELINE")
    return result
