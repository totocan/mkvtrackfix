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
            CREATE INDEX IF NOT EXISTS idx_movies_search ON movies(search_key);
            CREATE INDEX IF NOT EXISTS idx_movies_tmdb_id ON movies(tmdb_id);
            CREATE INDEX IF NOT EXISTS idx_movies_year ON movies(year);
            CREATE TABLE IF NOT EXISTS schema_version (version INTEGER);
            INSERT INTO schema_version (version) 
            SELECT {v} WHERE NOT EXISTS (SELECT 1 FROM schema_version);
        """.format(v=_SCHEMA_VERSION))
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
        # 候选：基键相同（含年份后缀），或基键是某行基键的前缀/后缀
        cand_sql = """
            SELECT *, substr(search_key, 1, instr(search_key||'|', '|')-1) AS base
            FROM movies
            WHERE base = ? OR base LIKE ? OR ? LIKE (base || '%')
        """
        rows = conn.execute(cand_sql, (key_base, key_base + "%", key_base)).fetchall()
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
        """零成本补 country_name：用 COUNTRY_MAP 把 ISO 代码转中文（v23.54 新增）。

        返回补齐条数。limit=None 表示全量。
        """
        conn = self._get_conn()
        sql = "SELECT id, country FROM movies WHERE country_name = '' AND country != ''"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql).fetchall()
        done = 0
        for r in rows:
            cn = COUNTRY_MAP.get((r["country"] or "").upper(), "")
            if cn:
                conn.execute("UPDATE movies SET country_name=? WHERE id=?",
                             (cn, r["id"]))
                done += 1
        conn.commit()
        return done

    def strengthen_missing(self, api_key, interval=20, stop_check=None,
                           on_log=None, on_progress=None, batch_limit=0):
        """自动强化（v23.54 新增，v23.55 支持高速档+429退避）：TMDB API 批量补中文名。

        - interval 为「每条间隔秒数」，支持小数（0.02 ≈ 50条/秒）
        - 遇 HTTP 429（限流）自动退避：等待 Retry-After 或 5 秒后重试，不中断
        - stop_check 返回 True 时中止；on_log/on_progress 回调
        返回 (processed, updated)。
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

        sql = "SELECT id, title_en, year FROM movies WHERE title_zh = '' AND title_en != ''"
        if batch_limit:
            sql += f" LIMIT {int(batch_limit)}"
        todo = conn.execute(sql).fetchall()
        total = len(todo)
        processed = updated = 0
        for r in todo:
            if stop_check and stop_check():
                if on_log:
                    on_log("⏹ 收到停止信号，中止强化。")
                break
            mid_title, myear, mid = r["title_en"], r["year"], r["id"]
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
        return processed, updated

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
        callback(row_count, total) 可选，用于显示进度。
        """
        import csv
        conn = self._get_conn()
        total = 0
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = []
            for row in reader:
                total += 1
                try:
                    year = None
                    if row.get("release_date"):
                        year = int(row["release_date"][:4])
                    title_en = row.get("title", "") or row.get("original_title", "")
                    if not title_en:
                        continue
                    key = self._normalize_key(title_en, year)
                    # 提取国家
                    country = ""
                    countries = row.get("production_countries", "[]")
                    try:
                        clist = json.loads(countries)
                        if clist:
                            country = clist[0].get("iso_3166_1", "")
                    except Exception:
                        pass
                    rows.append((
                        key, title_en, "", year, country, "",
                        row.get("original_language", ""),
                        row.get("id"), json.dumps(row, ensure_ascii=False),
                        "kaggle", datetime.datetime.now().isoformat()
                    ))
                except Exception:
                    pass
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
                        callback(total, 0)
            if rows:
                conn.executemany("""
                    INSERT OR IGNORE INTO movies
                    (search_key, title_en, title_zh, year, country,
                     country_name, language, tmdb_id, raw_json,
                     source, cached_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, rows)
                conn.commit()
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
    
    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
