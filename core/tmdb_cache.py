# -*- coding: utf-8 -*-
"""
TMDB 本地缓存模块 (v23.49)

提供一个 SQLite 数据库，缓存 TMDB 查询结果（电影 ID、中英文标题、年份、国家、语言）。
主程序和子程序共享同一个 .db 文件，不需要进程间通信。

用法:
    cache = TmdbCache()
    result = cache.lookup("A Fistful of Dollars", 1964)
    if not result:
        result = online_query(...)  # 调 TMDB
        cache.save("A Fistful of Dollars", 1964, result)
"""
import sqlite3
import os
import json
import datetime

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
CACHE_DB = os.path.join(CACHE_DIR, "tmdb_cache.db")
_SCHEMA_VERSION = 1


# ── ISO 3166-1 alpha-2 -> 中文国名 静态映射（v23.54 强化） ──
# 覆盖 TMDB production_countries 常见代码，用于零成本补 country_name，
# 不依赖联网爬取。缺失代码回退为空字符串（保持原值）。
COUNTRY_MAP = {
    "US": "美国", "GB": "英国", "FR": "法国", "DE": "德国", "IT": "意大利",
    "ES": "西班牙", "JP": "日本", "KR": "韩国", "CN": "中国", "HK": "中国香港",
    "TW": "中国台湾", "RU": "俄罗斯", "CA": "加拿大", "AU": "澳大利亚", "NZ": "新西兰",
    "IN": "印度", "BR": "巴西", "MX": "墨西哥", "AR": "阿根廷", "CH": "瑞士",
    "SE": "瑞典", "NO": "挪威", "DK": "丹麦", "FI": "芬兰", "NL": "荷兰",
    "BE": "比利时", "AT": "奥地利", "PL": "波兰", "CZ": "捷克", "HU": "匈牙利",
    "PT": "葡萄牙", "IE": "爱尔兰", "GR": "希腊", "TR": "土耳其", "TH": "泰国",
    "SG": "新加坡", "MY": "马来西亚", "ID": "印度尼西亚", "PH": "菲律宾", "VN": "越南",
    "ZA": "南非", "EG": "埃及", "IL": "以色列", "SA": "沙特阿拉伯", "AE": "阿联酋",
    "UA": "乌克兰", "RO": "罗马尼亚", "BG": "保加利亚", "HR": "克罗地亚", "RS": "塞尔维亚",
    "SK": "斯洛伐克", "SI": "斯洛文尼亚", "LT": "立陶宛", "LV": "拉脱维亚", "EE": "爱沙尼亚",
    "IS": "冰岛", "LU": "卢森堡", "MC": "摩纳哥", "AD": "安道尔", "MT": "马耳他",
    "CY": "塞浦路斯", "QA": "卡塔尔", "KW": "科威特", "LB": "黎巴嫩", "JO": "约旦",
    "MA": "摩洛哥", "TN": "突尼斯", "KE": "肯尼亚", "NG": "尼日利亚", "GH": "加纳",
    "CO": "哥伦比亚", "CL": "智利", "PE": "秘鲁", "VE": "委内瑞拉", "EC": "厄瓜多尔",
    "UY": "乌拉圭", "BO": "玻利维亚", "PY": "巴拉圭", "CR": "哥斯达黎加", "PA": "巴拿马",
    "CU": "古巴", "DO": "多米尼加", "GT": "危地马拉", "PK": "巴基斯坦", "BD": "孟加拉国",
    "LK": "斯里兰卡", "NP": "尼泊尔", "KH": "柬埔寨", "MM": "缅甸", "MN": "蒙古",
    "KZ": "哈萨克斯坦", "GE": "格鲁吉亚", "AM": "亚美尼亚", "AZ": "阿塞拜疆", "BY": "白俄罗斯",
    "IR": "伊朗", "IQ": "伊拉克", "SY": "叙利亚", "AF": "阿富汗", "KP": "朝鲜",
    "MO": "中国澳门", "PR": "波多黎各", "JM": "牙买加", "BS": "巴哈马", "TT": "特立尼达和多巴哥",
    "LU": "卢森堡", "LI": "列支敦士登", "SM": "圣马力诺", "VA": "梵蒂冈", "FO": "法罗群岛",
    "GL": "格陵兰", "BM": "百慕大", "KY": "开曼群岛", "EU": "欧洲", "XWG": "西德",
}


# ── TMDB genre id -> 中文类型 静态映射 ──
GENRE_MAP = {
    28: "动作", 12: "冒险", 16: "动画", 35: "喜剧", 80: "犯罪",
    99: "纪录", 18: "剧情", 10751: "家庭", 14: "奇幻", 36: "历史",
    27: "恐怖", 10402: "音乐", 9648: "悬疑", 10749: "爱情", 878: "科幻",
    10770: "电视电影", 53: "惊悚", 10752: "战争", 37: "西部",
}


# ── English country name -> 中文国名 静态映射（v23.56 补） ──
# Kaggle CSV 的 production_countries[].name 是英文全名（如 "United States of America"），
# COUNTRY_MAP 只有 ISO 码不够用。补这张表让本地批处理直接转英文为中文。
EN_COUNTRY_MAP = {
    "Afghanistan": "阿富汗", "Albania": "阿尔巴尼亚", "Algeria": "阿尔及利亚",
    "Argentina": "阿根廷", "Armenia": "亚美尼亚", "Australia": "澳大利亚",
    "Austria": "奥地利", "Azerbaijan": "阿塞拜疆", "Bahrain": "巴林",
    "Bangladesh": "孟加拉国", "Belarus": "白俄罗斯", "Belgium": "比利时",
    "Bolivia": "玻利维亚", "Bosnia and Herzegovina": "波黑",
    "Brazil": "巴西", "Bulgaria": "保加利亚", "Cambodia": "柬埔寨",
    "Canada": "加拿大", "Chile": "智利", "China": "中国", "Colombia": "哥伦比亚",
    "Costa Rica": "哥斯达黎加", "Croatia": "克罗地亚", "Cuba": "古巴",
    "Cyprus": "塞浦路斯", "Czech Republic": "捷克", "Czechia": "捷克",
    "Denmark": "丹麦", "Dominican Republic": "多米尼加",
    "Ecuador": "厄瓜多尔", "Egypt": "埃及", "El Salvador": "萨尔瓦多",
    "Estonia": "爱沙尼亚", "Ethiopia": "埃塞俄比亚", "Finland": "芬兰",
    "France": "法国", "Georgia": "格鲁吉亚", "Germany": "德国", "Ghana": "加纳",
    "Greece": "希腊", "Guatemala": "危地马拉", "Hong Kong": "中国香港",
    "Hungary": "匈牙利", "Iceland": "冰岛", "India": "印度", "Indonesia": "印度尼西亚",
    "Iran": "伊朗", "Iraq": "伊拉克", "Ireland": "爱尔兰", "Israel": "以色列",
    "Italy": "意大利", "Jamaica": "牙买加", "Japan": "日本", "Jordan": "约旦",
    "Kazakhstan": "哈萨克斯坦", "Kenya": "肯尼亚", "Kuwait": "科威特",
    "Kyrgyzstan": "吉尔吉斯斯坦", "Laos": "老挝", "Latvia": "拉脱维亚",
    "Lebanon": "黎巴嫩", "Libya": "利比亚", "Lithuania": "立陶宛",
    "Luxembourg": "卢森堡", "Macao": "中国澳门", "Malaysia": "马来西亚",
    "Malta": "马耳他", "Mexico": "墨西哥", "Moldova": "摩尔多瓦",
    "Monaco": "摩纳哥", "Mongolia": "蒙古", "Montenegro": "黑山",
    "Morocco": "摩洛哥", "Myanmar": "缅甸", "Nepal": "尼泊尔",
    "Netherlands": "荷兰", "New Zealand": "新西兰", "Nicaragua": "尼加拉瓜",
    "Nigeria": "尼日利亚", "North Korea": "朝鲜", "Norway": "挪威",
    "Pakistan": "巴基斯坦", "Palestine": "巴勒斯坦", "Panama": "巴拿马",
    "Paraguay": "巴拉圭", "Peru": "秘鲁", "Philippines": "菲律宾",
    "Poland": "波兰", "Portugal": "葡萄牙", "Puerto Rico": "波多黎各",
    "Qatar": "卡塔尔", "Romania": "罗马尼亚", "Russia": "俄罗斯",
    "Saudi Arabia": "沙特阿拉伯", "Serbia": "塞尔维亚", "Singapore": "新加坡",
    "Slovakia": "斯洛伐克", "Slovenia": "斯洛文尼亚", "South Africa": "南非",
    "South Korea": "韩国", "Spain": "西班牙", "Sri Lanka": "斯里兰卡",
    "Sweden": "瑞典", "Switzerland": "瑞士", "Syria": "叙利亚",
    "Taiwan": "中国台湾", "Tajikistan": "塔吉克斯坦", "Tanzania": "坦桑尼亚",
    "Thailand": "泰国", "Tunisia": "突尼斯", "Turkey": "土耳其",
    "Turkmenistan": "土库曼斯坦", "Uganda": "乌干达", "Ukraine": "乌克兰",
    "United Arab Emirates": "阿联酋", "United Kingdom": "英国",
    "United States of America": "美国", "Uruguay": "乌拉圭",
    "Uzbekistan": "乌兹别克斯坦", "Venezuela": "委内瑞拉", "Vietnam": "越南",
    "Yemen": "也门", "Zimbabwe": "津巴布韦", "Soviet Union": "苏联",
    "East Germany": "东德", "West Germany": "西德", "Yugoslavia": "南斯拉夫",
    "Czechoslovakia": "捷克斯洛伐克",
}


class TmdbCache:
    def __init__(self, db_path=None):
        self.db_path = db_path or CACHE_DB
        self._conn = None
        self._init_db()
    
    def _get_conn(self):
        if self._conn is None:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            self._conn = sqlite3.connect(self.db_path, timeout=10)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn
    
    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS movies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                search_key TEXT NOT NULL,        -- 归一化的搜索关键字
                title_en TEXT,                    -- 英文标题
                title_zh TEXT,                    -- 中文标题
                year INTEGER,                     -- 年份
                country TEXT,                     -- ISO 3166-1 国家代码
                country_name TEXT,                -- 中文国家名称
                language TEXT,                    -- ISO 639-1 语言代码
                tmdb_id INTEGER,                 -- TMDB 电影 ID
                raw_json TEXT,                    -- 完整的 TMDB 响应 JSON
                source TEXT DEFAULT 'tmdb',       -- 数据来源: tmdb / kaggle / manual
                cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_movies_search ON movies(search_key COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_movies_tmdb_id ON movies(tmdb_id);
            CREATE INDEX IF NOT EXISTS idx_movies_year ON movies(year);
            CREATE TABLE IF NOT EXISTS schema_version (version INTEGER);
            INSERT INTO schema_version (version)
            SELECT {v} WHERE NOT EXISTS (SELECT 1 FROM schema_version);
        """.format(v=_SCHEMA_VERSION))
        # 收集统计信息，让查询优化器为索引选择更准确（v23.55：老库打开自动补）
        try:
            conn.execute("ANALYZE")
        except Exception:
            pass
        conn.commit()
    
    def _normalize_key(self, title, year=None):
        """归一化标题用于搜索：小写、去空格、去特殊字符"""
        key = title.strip().lower()
        # 去除非字母数字字符
        key = ''.join(c for c in key if c.isalnum() or c.isspace())
        key = ' '.join(key.split())  # 合并多余空格
        if year:
            key = f"{key}|{year}"
        return key
    
    def lookup(self, title, year=None):
        """按标题+年份查缓存，返回 dict 或 None"""
        key = self._normalize_key(title, year)
        conn = self._get_conn()
        c = conn.execute(
            "SELECT * FROM movies WHERE search_key = ? LIMIT 1", (key,))
        row = c.fetchone()
        if row:
            return dict(row)
        # 没有年份时尝试匹配标题
        if not year:
            c = conn.execute(
                "SELECT * FROM movies WHERE search_key LIKE ? LIMIT 1",
                (key + "|%",))
            row = c.fetchone()
            if row:
                return dict(row)
        return None
    
    def search_fuzzy(self, title, year=None):
        """模糊搜索：移除年份后缀后匹配标题。返回列表（最多 5 条）"""
        key_base = self._normalize_key(title)
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM movies WHERE search_key LIKE ? ORDER BY year DESC LIMIT 5",
            (key_base + "%",)).fetchall()
        # 如果有年份，优先匹配年份
        if year:
            exact = [r for r in rows if r["year"] == year]
            if exact:
                return [dict(r) for r in exact]
        return [dict(r) for r in rows]
    
    def search_broad(self, title, year=None, year_max=None, country=None,
                     genre=None, page=1, page_size=100):
        """分级泛搜索（v23.54 新增，v23.55 扩年份范围+类型中文）。

        匹配优先级（用 level 标注）：
          [精确]  search_key 完全相等（标题+年份）
          [同年]  标题基键相同、年份不同
          [模糊]  标题基键为前缀（如输入3词，库里有更长/更短包含）
        筛选：year(精确) / year_max(<=上限,如"2020年以前") / country / genre(中英文均可)。
        分页：每页 page_size（默认100），返回 {total, page, page_size, rows:[{...,level}]}。
        """
        # 剥离标题中内嵌的 4 位年份（如 "casino.royale.1967"），避免年份被重复计入键
        import re as _re
        _m = _re.search(r'(?:^|[\s._-])(\d{4})(?:[\s._-]|$)', title)
        if _m and not year:
            year = int(_m.group(1))
        # 先把分隔符(./_/-)统一为空格，再删年份，避免 "casino.royale" 被粘成 "casinoroyale"
        _sep = _re.sub(r'[\._\-]', ' ', title)
        _stripped = _re.sub(r'(?:^|[\s._-])\d{4}(?:[\s._-]|$)', ' ', _sep)
        _stripped = ' '.join(_stripped.split())
        key_base = self._normalize_key(_stripped)
        conn = self._get_conn()
        # 候选：优先用 search_key 上的索引（idx_movies_search），避免对 96 万行
        # 计算列 base 全表扫描导致 OOM/卡死。
        # search_key 格式 "casino royale|1967"，前缀 "casino royale|%" 命中索引，
        # 覆盖[精确]+[同年]；模糊含入用 "%casino royale%" 兜底。
        cand_sql = """
            SELECT *
            FROM movies
            WHERE search_key LIKE ? OR search_key LIKE ?
            LIMIT 5000
        """
        rows = conn.execute(cand_sql, (key_base + "|%", "%" + key_base + "%")).fetchall()
        rows = [dict(r) for r in rows]

        # 分级
        exact, same_title, fuzzy = [], [], []
        for r in rows:
            rk = r["search_key"]
            rbase = rk.split("|")[0]
            ryear = int(rk.split("|")[1]) if "|" in rk and rk.split("|")[1].isdigit() else None
            if rk == key_base + (f"|{year}" if year else ""):
                r["level"] = "精确"; exact.append(r)
            elif rbase == key_base:
                r["level"] = "同年"; same_title.append(r)
            else:
                r["level"] = "模糊"; fuzzy.append(r)

        # 年份筛选（精确 year 优先；否则 year_max 范围）
        if year:
            same_title = [r for r in same_title if r.get("year") == year] + \
                         [r for r in same_title if r.get("year") != year]
        # 国家筛选（ISO 或中文名）
        def _match_country(r):
            if not country:
                return True
            c = country.strip()
            return (r.get("country", "").upper() == c.upper()) or \
                   (r.get("country_name", "") == c)
        # 类型筛选（中文名或英文名均可 -> 需解析 raw_json 的 genres）
        # Kaggle 导入时 genres 是 JSON 字符串，需二次解析
        _zh_to_en = {v: k for k, v in GENRE_MAP.items()}
        def _match_genre(r):
            if not genre:
                return True
            g = genre.strip()
            g_en = _zh_to_en.get(g, g)  # 若传入是中文，转回英文比对；英文名直接用
            try:
                raw = json.loads(r.get("raw_json") or "{}")
                genres = raw.get("genres") or raw.get("genre_ids") or []
                if isinstance(genres, str):
                    try:
                        genres = json.loads(genres)
                    except Exception:
                        genres = []
                names = []
                _en_to_zh = {v: k for k, v in GENRE_MAP.items()}
                for x in genres:
                    if isinstance(x, dict):
                        nm = x.get("name", "")
                        zh = GENRE_MAP.get(x.get("id")) if x.get("id") else _en_to_zh.get(nm)
                        if zh:
                            names.append(zh)
                        if nm:
                            names.append(nm)
                    elif isinstance(x, int):
                        names.append(GENRE_MAP.get(x, ""))
                return (g in names) or (g_en in names)
            except Exception:
                return False

        merged = []
        for bucket in (exact, same_title, fuzzy):
            for r in bucket:
                if not _match_country(r):
                    continue
                if not _match_genre(r):
                    continue
                if year_max is not None and (r.get("year") or 0) > year_max:
                    continue
                merged.append(r)
        total = len(merged)
        start = (page - 1) * page_size
        page_rows = merged[start:start + page_size]
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": (total + page_size - 1) // page_size or 1,
            "rows": page_rows,
        }

    def distinct_genres(self):
        """汇总库里出现过的类型（返回中文名，供筛选下拉用）。

        Kaggle raw_json 的 genres[].name 是英文（如 "Action"），
        用 GENRE_MAP 反查成中文（"动作"）。查不到中文的英文原名也保留。
        """
        conn = self._get_conn()
        names = set()
        for (raw,) in conn.execute(
                "SELECT raw_json FROM movies WHERE raw_json != '' LIMIT 50000").fetchall():
            try:
                d = json.loads(raw)
                genres = d.get("genres") or []
                if isinstance(genres, str):
                    try:
                        genres = json.loads(genres)
                    except Exception:
                        genres = []
                for x in genres:
                    if isinstance(x, dict) and (x.get("name") or x.get("id")):
                        # 优先用 id 查中文（GENRE_MAP key 为 int id）
                        zh = GENRE_MAP.get(x.get("id")) if x.get("id") else None
                        if not zh and x.get("name"):
                            # 退而用英文名反向查（GENRE_MAP 值是中文，建反查）
                            _en_to_zh = {v: k for k, v in GENRE_MAP.items()}
                            zh = _en_to_zh.get(x["name"])
                        names.add(zh if zh else x.get("name"))
            except Exception:
                continue
        return sorted(names, key=lambda s: s)

    def apply_country_names(self, limit=None):
        """本地批处理把 country_name 转中文（v23.54 新增，v23.56 同时支持英文名和 ISO 码）。

        路径 1：country_name 已是英文全名（如 "United States of America"）→ EN_COUNTRY_MAP 转中文
        路径 2：country_name 为空但 country（ISO 码）有值 → COUNTRY_MAP 转中文
        已是中国的不动。
        返回补齐条数。limit=None 表示全量。
        """
        conn = self._get_conn()
        # 中文集合（已转完的跳过）
        zh_values = set(COUNTRY_MAP.values()) | set(EN_COUNTRY_MAP.values())
        # 路径 1：英文名（country_name 非空 且 非中文）
        sql1 = "SELECT id, country_name FROM movies WHERE country_name IS NOT NULL AND country_name != ''"
        if limit:
            sql1 += f" LIMIT {int(limit)}"
        done = 0
        for r in conn.execute(sql1).fetchall():
            name = (r["country_name"] or "").strip()
            if not name or name in zh_values:
                continue
            # 先按英文名查
            cn = EN_COUNTRY_MAP.get(name) or COUNTRY_MAP.get(name.upper())
            # 英文表里没有但 ISO 码有（比如 "USA" → 美国）
            if not cn and r["country_name"] in COUNTRY_MAP:
                cn = COUNTRY_MAP[r["country_name"]]
            if cn and cn != name:
                conn.execute("UPDATE movies SET country_name=? WHERE id=?",
                             (cn, r["id"]))
                done += 1
        # 路径 2：ISO 码有但 country_name 空（极少，因为 Kaggle 也存了 name）
        sql2 = ("SELECT id, country FROM movies "
                "WHERE IFNULL(country_name,'') = '' AND IFNULL(country,'') != ''")
        if limit:
            sql2 += f" LIMIT {int(limit)}"
        for r in conn.execute(sql2).fetchall():
            cn = COUNTRY_MAP.get((r["country"] or "").upper(), "")
            if cn:
                conn.execute("UPDATE movies SET country_name=? WHERE id=?",
                             (cn, r["id"]))
                done += 1
        conn.commit()
        return done

    def backfill_country_from_raw_json(self, limit=None):
        """从 raw_json（原始 CSV 行 JSON）反向补 country / country_name。

        适用：Kaggle 导入时只取了 iso_3166_1 没取 name，导致 country_name 全空。
        原始 CSV 行的 production_countries JSON 在 raw_json 里存着，
        可以从 production_countries[0] 同时拿出 iso_3166_1 和 name 回填。
        返回补齐条数。
        """
        conn = self._get_conn()
        sql = ("SELECT id, raw_json FROM movies "
               "WHERE IFNULL(country_name,'') = '' "
               "AND raw_json IS NOT NULL AND raw_json != ''")
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql).fetchall()
        done = 0
        for r in rows:
            try:
                d = json.loads(r["raw_json"] or "{}")
                pcs = d.get("production_countries")
                if isinstance(pcs, str):
                    pcs = json.loads(pcs)
                if not pcs or not isinstance(pcs, list) or not pcs[0]:
                    continue
                first = pcs[0]
                country = (first.get("iso_3166_1", "") or "").strip()
                country_name = (first.get("name", "") or "").strip()
                if not country_name and country:
                    country_name = COUNTRY_MAP.get(country.upper(), "")
                if country or country_name:
                    conn.execute(
                        "UPDATE movies SET country=?, country_name=? WHERE id=?",
                        (country, country_name, r["id"]))
                    done += 1
            except Exception:
                continue
        conn.commit()
        return done

    def strengthen_missing(self, api_key, interval=20, stop_check=None,
                           on_log=None, on_progress=None, batch_limit=0,
                           start_after_id=0):
        """自动强化（v23.54 新增，v23.55 支持高速档+429退避）：TMDB API 批量补中文名。

        - interval 为「每条间隔秒数」，支持小数（0.02 ≈ 50条/秒）
        - 遇 HTTP 429（限流）自动退避：等待 Retry-After 或 5 秒后重试，不中断
        - stop_check 返回 True 时中止；on_log/on_progress 回调
        - start_after_id：续跑起点（id > 该值），用于断点续传
        返回 (processed, updated, last_id)。
        """
        import requests
        import time
        conn = self._get_conn()

        def _get(url, params, retries=3):
            for attempt in range(retries):
                try:
                    resp = requests.get(url, params=params, timeout=15)
                except Exception:
                    time.sleep(2)
                    continue
                if resp.status_code == 429:
                    # 限流：退避后重试
                    wait = 5
                    try:
                        if resp.headers.get("Retry-After"):
                            wait = int(resp.headers["Retry-After"])
                    except Exception:
                        pass
                    if on_log:
                        on_log(f"  ⏳ 429 限流，退避 {wait}s 后重试")
                    time.sleep(wait)
                    continue
                return resp
            return None

        # 断点续传：从 start_after_id 之后开始；ORDER BY id 保证确定性顺序
        sql = ("SELECT id, title_en, year FROM movies "
               "WHERE title_zh = '' AND title_en != '' AND id > ? "
               "ORDER BY id")
        if batch_limit:
            sql += f" LIMIT {int(batch_limit)}"
        todo = conn.execute(sql, (start_after_id,)).fetchall()
        total = len(todo)
        processed = updated = 0
        last_id = start_after_id
        for r in todo:
            if stop_check and stop_check():
                if on_log:
                    on_log("⏹ 收到停止信号，中止强化。")
                break
            mid_title, myear, mid = r["title_en"], r["year"], r["id"]
            last_id = mid
            try:
                s_url = "https://api.themoviedb.org/3/search/movie"
                params = {"api_key": api_key, "query": mid_title,
                          "language": "zh-CN", "include_adult": False}
                if myear:
                    params["year"] = myear
                resp = _get(s_url, params)
                if resp is None:
                    processed += 1
                    time.sleep(interval)
                    continue
                if resp.status_code != 200:
                    if on_log:
                        on_log(f"  ✗ [{mid_title}] API {resp.status_code}")
                    processed += 1
                    time.sleep(interval)
                    continue
                sdata = resp.json()
                res = (sdata.get("results") or [])
                if not res:
                    if on_log:
                        on_log(f"  · [{mid_title}] 无搜索结果")
                    processed += 1
                    time.sleep(interval)
                    continue
                top = res[0]
                title_zh = top.get("title") or top.get("original_title") or ""
                tmdb_id = top.get("id")
                country_name = ""
                country = ""
                # 2) 详情补国家（production_countries）
                if tmdb_id:
                    d_url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
                    dresp = _get(d_url,
                                 {"api_key": api_key, "language": "zh-CN"})
                    if dresp is not None and dresp.status_code == 200:
                        dd = dresp.json()
                        pcs = dd.get("production_countries") or []
                        if pcs:
                            country = pcs[0].get("iso_3166_1", "")
                            country_name = pcs[0].get("name", "") or \
                                COUNTRY_MAP.get(country.upper(), "")
                if not country_name and country:
                    country_name = COUNTRY_MAP.get(country.upper(), "")
                if title_zh or country_name:
                    conn.execute("""
                        UPDATE movies SET title_zh=?, country=?, country_name=?,
                        tmdb_id=?, updated_at=? WHERE id=?
                    """, (title_zh, country, country_name,
                          tmdb_id, datetime.datetime.now().isoformat(), mid))
                    conn.commit()
                    updated += 1
                    if on_log:
                        on_log(f"  ✓ [{mid_title}] zh={title_zh} cn={country_name}")
                else:
                    if on_log:
                        on_log(f"  · [{mid_title}] 无中文信息")
            except Exception as e:
                if on_log:
                    on_log(f"  ✗ [{mid_title}] 异常 {e}")
            processed += 1
            if on_progress:
                on_progress(processed, total, updated)
            time.sleep(interval)
        return processed, updated, last_id

    def save(self, title, year, data):
        """保存一条 TMDB 查询结果到缓存。data 为 dict"""
        key = self._normalize_key(title, year)
        conn = self._get_conn()
        now = datetime.datetime.now().isoformat()
        # 检查是否已存在
        existing = conn.execute(
            "SELECT id FROM movies WHERE search_key = ?", (key,)).fetchone()
        if existing:
            conn.execute("""
                UPDATE movies SET
                    title_en=?, title_zh=?, country=?, country_name=?,
                    language=?, tmdb_id=?, raw_json=?, source=?,
                    updated_at=?
                WHERE search_key=?
            """, (
                data.get("title_en", ""),
                data.get("title_zh", ""),
                data.get("country", ""),
                data.get("country_name", ""),
                data.get("language", ""),
                data.get("tmdb_id"),
                json.dumps(data, ensure_ascii=False),
                data.get("source", "tmdb"),
                now,
                key
            ))
        else:
            conn.execute("""
                INSERT INTO movies 
                (search_key, title_en, title_zh, year, country, country_name,
                 language, tmdb_id, raw_json, source, cached_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                key,
                data.get("title_en", ""),
                data.get("title_zh", ""),
                year,
                data.get("country", ""),
                data.get("country_name", ""),
                data.get("language", ""),
                data.get("tmdb_id"),
                json.dumps(data, ensure_ascii=False),
                data.get("source", "tmdb"),
                now, now
            ))
        conn.commit()
    
    def import_kaggle_csv(self, csv_path, callback=None):
        """导入 Kaggle CSV 数据集（批量入库）。

        CSV 需包含: id, title, release_date, original_language,
        production_countries(JSON), original_title 等列。
        callback(row_count, total) 用于显示进度（total 为真实总行数）。
        v23.55 修复：field_size_limit 防超长字段卡死；坏行跳过不中断。
        """
        import csv
        import sys as _sys
        csv.field_size_limit(min(getattr(_sys, "maxsize", 2**31 - 1), 2**31 - 1))
        conn = self._get_conn()
        # 先数总行数（用于真实百分比进度）
        _total_lines = 0
        try:
            with open(csv_path, 'r', encoding='utf-8', errors='replace') as _fc:
                _total_lines = sum(1 for _ in _fc) - 1  # 去掉表头
        except Exception:
            _total_lines = 0
        total = 0
        skipped = 0
        with open(csv_path, 'r', encoding='utf-8', errors='replace') as f:
            reader = csv.DictReader(f)
            rows = []
            for row in reader:
                total += 1
                try:
                    year = None
                    if row.get("release_date"):
                        year = int(str(row["release_date"])[:4])
                    title_en = (row.get("title") or "").strip() or \
                               (row.get("original_title") or "").strip()
                    if not title_en:
                        skipped += 1
                        continue
                    key = self._normalize_key(title_en, year)
                    country = ""
                    country = ""
                    country_name = ""
                    countries = row.get("production_countries", "[]") or "[]"
                    try:
                        clist = json.loads(countries)
                        if clist:
                            country = (clist[0].get("iso_3166_1", "") or "").strip()
                            country_name = (clist[0].get("name", "") or "").strip()
                            if not country_name and country:
                                country_name = COUNTRY_MAP.get(country.upper(), "")
                    except Exception:
                        pass
                    rows.append((
                        key, title_en, "", year, country, country_name,
                        row.get("original_language", ""),
                        row.get("id"), json.dumps(row, ensure_ascii=False),
                        "kaggle", datetime.datetime.now().isoformat()
                    ))
                except Exception:
                    skipped += 1
                    continue
                if len(rows) >= 500:
                    conn.executemany("""
                        INSERT OR IGNORE INTO movies
                        (search_key, title_en, title_zh, year, country,
                         country_name, language, tmdb_id, raw_json,
                         source, cached_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """, rows)
                    conn.commit()
                    rows = []
                    if callback:
                        callback(total, _total_lines)
            if rows:
                conn.executemany("""
                    INSERT OR IGNORE INTO movies
                    (search_key, title_en, title_zh, year, country,
                     country_name, language, tmdb_id, raw_json,
                     source, cached_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, rows)
                conn.commit()
        if callback:
            callback(total, _total_lines)
        return total
    
    def stats(self):
        """返回缓存统计信息"""
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
        with_zh = conn.execute(
            "SELECT COUNT(*) FROM movies WHERE title_zh != ''").fetchone()[0]
        sources = conn.execute(
            "SELECT source, COUNT(*) FROM movies GROUP BY source").fetchall()
        return {
            "total": total,
            "with_chinese_title": with_zh,
            "by_source": dict(sources),
        }
    
    def index_status(self):
        """返回索引状态（是否已建索引、行数、索引大小等）。

        用于界面告知用户：当前库是否建立了搜索所需的索引。
        缺失 idx_movies_search 时，泛搜索会退化为全表扫描（大库极慢甚至 OOM）。
        """
        conn = self._get_conn()
        try:
            row_count = conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
        except Exception:
            row_count = 0
        # 列出 movies 表上的全部索引
        idx_names = set()
        try:
            for (nm,) in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='movies'").fetchall():
                idx_names.add(nm)
        except Exception:
            pass
        # 各索引占用空间（依赖 dbstat 虚拟表，SQLite 3.16+ 默认可用）
        idx_sizes = {}
        try:
            for nm, pgsz in conn.execute(
                    "SELECT name, SUM(pgsize) FROM dbstat "
                    "WHERE name IN (SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='movies') GROUP BY name"
            ).fetchall():
                idx_sizes[nm] = pgsz
        except Exception:
            pass
        db_size = 0
        try:
            db_size = os.path.getsize(self.db_path)
        except Exception:
            pass
        return {
            "row_count": row_count,
            "db_size": db_size,
            "indexes": sorted(idx_names),
            "has_search_index": "idx_movies_search" in idx_names,
            "has_tmdb_id_index": "idx_movies_tmdb_id" in idx_names,
            "has_year_index": "idx_movies_year" in idx_names,
            "index_sizes": idx_sizes,
        }

    def build_search_index(self, on_progress=None, on_log=None, drop_first=True):
        """手动（重建）TMDB 缓存库的全部索引。

        - drop_first=True 时先 DROP 再 CREATE，确保真正重建（例如从别人复制来的
          旧库可能缺索引，或统计信息过期需要 ANALYZE）。
        - on_progress(step, total_steps, phase_name) 报告阶段进度；
          on_log(msg) 报告日志。
        说明：CREATE INDEX 是原子操作，SQLite 不提供逐行进度，故以「阶段」为粒度：
              建 3 个索引 + ANALYZE，共 4 步。耗时取决于行数（百万级可能数十秒）。
        """
        conn = self._get_conn()
        steps = ["idx_movies_search", "idx_movies_tmdb_id", "idx_movies_year", "ANALYZE"]
        total = len(steps)
        rc = 0
        try:
            rc = conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
        except Exception:
            pass
        if on_log:
            on_log(f"🔧 开始构建索引（共 {total} 步，当前 {rc:,} 行）...")
        # 先 DROP（仅当存在），保证重建
        if drop_first:
            for nm in ("idx_movies_search", "idx_movies_tmdb_id", "idx_movies_year"):
                try:
                    conn.execute(f"DROP INDEX IF EXISTS {nm}")
                except Exception as e:
                    if on_log:
                        on_log(f"  · 删除旧索引 {nm} 失败（可忽略）: {e}")
            try:
                conn.commit()
            except Exception:
                pass
        creates = [
            ("idx_movies_search", "CREATE INDEX idx_movies_search ON movies(search_key COLLATE NOCASE)"),
            ("idx_movies_tmdb_id", "CREATE INDEX idx_movies_tmdb_id ON movies(tmdb_id)"),
            ("idx_movies_year", "CREATE INDEX idx_movies_year ON movies(year)"),
        ]
        for i, (nm, sql) in enumerate(creates, start=1):
            if on_log:
                on_log(f"  ⏳ [{i}/{total}] 建立 {nm} ...")
            if on_progress:
                on_progress(i, total, f"建立 {nm}")
            try:
                conn.execute(sql)
                conn.commit()
                if on_log:
                    on_log(f"  ✓ [{i}/{total}] {nm} 完成")
            except Exception as e:
                if on_log:
                    on_log(f"  ✗ [{i}/{total}] {nm} 失败: {e}")
                raise
        if on_log:
            on_log(f"  ⏳ [{total}/{total}] 更新统计信息 ANALYZE ...")
        if on_progress:
            on_progress(total, total, "ANALYZE")
        try:
            conn.execute("ANALYZE")
            conn.commit()
            if on_log:
                on_log(f"  ✓ [{total}/{total}] ANALYZE 完成")
        except Exception as e:
            if on_log:
                on_log(f"  ✗ ANALYZE 失败（可忽略）: {e}")
        if on_log:
            on_log("✅ 索引构建完成")

    def browse_rows(self, filters=None, page=1, page_size=200):
        """分页浏览 movies 表（DB 浏览器用）。

        filters: dict，可含 keyword(标题包含) / year_from / year_to /
                 zh('all'|'has'|'missing') / source(来源，None=全部)
        返回 (rows: list[dict], total: int)。
        """
        filters = filters or {}
        where, params = self._browse_where(filters)
        conn = self._get_conn()
        total = conn.execute(f"SELECT COUNT(*) FROM movies {where}", params).fetchone()[0]
        off = max(0, (page - 1) * page_size)
        cols = ("id", "title_en", "title_zh", "year", "country_name",
                "language", "source", "cached_at")
        sql = (f"SELECT {', '.join(cols)} FROM movies {where} "
               f"ORDER BY id LIMIT ? OFFSET ?")
        rows = conn.execute(sql, params + [page_size, off]).fetchall()
        return [dict(r) for r in rows], total

    def _browse_where(self, filters):
        where = "WHERE 1=1"
        params = []
        kw = (filters.get("keyword") or "").strip()
        if kw:
            where += " AND (title_en LIKE ? OR title_zh LIKE ?)"
            params += [f"%{kw}%", f"%{kw}%"]
        yf = filters.get("year_from")
        if yf:
            where += " AND year >= ?"
            params.append(int(yf))
        yt = filters.get("year_to")
        if yt:
            where += " AND year <= ?"
            params.append(int(yt))
        zh = filters.get("zh", "all")
        if zh == "has":
            where += " AND title_zh != ''"
        elif zh == "missing":
            where += " AND title_zh = ''"
        src = filters.get("source")
        if src:
            where += " AND source = ?"
            params.append(src)
        return where, params

    def export_rows(self, filters, csv_path, callback=None):
        """将筛选结果导出为 CSV（后台流式写入，避免大库占内存）。

        callback(written, total) 报告进度；返回写入行数。
        """
        import csv as _csv
        where, params = self._browse_where(filters)
        conn = self._get_conn()
        total = conn.execute(f"SELECT COUNT(*) FROM movies {where}", params).fetchone()[0]
        cols = ("id", "title_en", "title_zh", "year", "country", "country_name",
                "language", "tmdb_id", "source", "cached_at", "updated_at")
        written = 0
        try:
            with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
                w = _csv.writer(f)
                w.writerow(cols)
                for r in conn.execute(
                        f"SELECT {', '.join(cols)} FROM movies {where} ORDER BY id", params):
                    w.writerow([r[c] if r[c] is not None else "" for c in cols])
                    written += 1
                    if callback and written % 5000 == 0:
                        callback(written, total)
            if callback:
                callback(written, total)
        except Exception:
            if callback:
                callback(written, total)
            raise
        return written

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
