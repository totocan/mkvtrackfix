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
