# -*- coding: utf-8 -*-
"""
tmdb 缓存诊断脚本 (v23.55 临时调试)
不依赖 PyQt5，纯命令行。把脚本放在 tmdb_manager.py 同级目录运行。
输出：表结构、索引、search_key 样本、跑一次 search_broad 看耗时+是否崩。
"""
import os
import sys
import time
import sqlite3

DB_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "cache", "tmdb_cache.db")


def main():
    db = DB_DEFAULT
    if not os.path.exists(db):
        # 兼容 tmdb_agent 目录
        alt = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "Documents", "tmdb_agent", "cache", "tmdb_cache.db")
        print(f"默认 db 不存在: {db}")
        print(f"尝试: {alt}")
        return
    print("=" * 60)
    print(f"DB: {db}")
    print(f"大小: {os.path.getsize(db) / 1024 / 1024:.1f} MB")
    print("=" * 60)
    conn = sqlite3.connect(db, timeout=10)
    conn.row_factory = sqlite3.Row
    # 1) 表结构
    print("\n【1】表结构 (movies)")
    for r in conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='movies'"):
        print(r["sql"])
    # 2) 索引
    print("\n【2】索引")
    for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='movies'"):
        print(f"  {r['name']}: {r['sql']}")
    # 3) 总数
    n = conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
    print(f"\n【3】总条数: {n:,}")
    # 4) search_key 样本
    print("\n【4】search_key 样本 (前 10 条非空)")
    for r in conn.execute("SELECT id, search_key, title_en, year FROM movies WHERE search_key != '' LIMIT 10"):
        print(f"  id={r['id']:>8}  sk='{r['search_key']}'  en='{(r['title_en'] or '')[:40]}'  yr={r['year']}")
    # 5) search_key 异常
    print("\n【5】search_key 异常统计")
    empty = conn.execute("SELECT COUNT(*) FROM movies WHERE search_key IS NULL OR search_key = ''").fetchone()[0]
    no_pipe = conn.execute("SELECT COUNT(*) FROM movies WHERE search_key NOT LIKE '%|%'").fetchone()[0]
    print(f"  空/NULL: {empty:,}")
    print(f"  无年份分隔符: {no_pipe:,}")
    # 6) raw_json 解析
    print("\n【6】raw_json 解析失败率 (抽样 1000)")
    bad = 0
    for (raw,) in conn.execute("SELECT raw_json FROM movies WHERE raw_json != '' LIMIT 1000").fetchall():
        try:
            import json
            json.loads(raw)
        except Exception:
            bad += 1
    print(f"  解析失败: {bad}/1000")
    # 7) search_broad 实测
    print("\n【7】search_broad 实测")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from core.tmdb_cache import TmdbCache
        cache = TmdbCache(db)
        for q in ["casino", "angel", "the matrix"]:
            t0 = time.time()
            try:
                r = cache.search_broad(q)
                dt = time.time() - t0
                print(f"  search_broad('{q}'): total={r['total']:,} 耗时={dt:.3f}s 异常=无")
            except Exception as e:
                print(f"  search_broad('{q}'): 异常 {type(e).__name__}: {e}")
    except Exception as e:
        print(f"  无法导入 TmdbCache: {e}")
    # 8) 原始 SQL 跑一下，看 EXPLAIN QUERY PLAN
    print("\n【8】EXPLAIN QUERY PLAN (search_key 索引命中?)")
    for q in ["casino", "angel"]:
        t0 = time.time()
        sql = "SELECT id, search_key FROM movies WHERE search_key LIKE ? OR search_key LIKE ? LIMIT 5"
        try:
            plan = list(conn.execute(f"EXPLAIN QUERY PLAN {sql}", (q + "|%", "%" + q + "%")))
            dt = time.time() - t0
            for p in plan:
                print(f"  '{q}': {dict(p)}")
        except Exception as e:
            print(f"  '{q}': {e}")
    print("\n诊断完成。")


if __name__ == "__main__":
    main()
