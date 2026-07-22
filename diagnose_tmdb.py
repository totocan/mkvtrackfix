# -*- coding: utf-8 -*-
"""
tmdb 缓存诊断脚本 (v23.55)
用法:
  python diagnose_tmdb.py
  python diagnose_tmdb.py "C:\Users\R2\Documents\tmdb_agent\cache\tmdb_cache.db"
"""
import os
import sys
import time
import sqlite3
import traceback


def find_default_db():
    # 脚本所在目录的 cache 子目录
    p1 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "tmdb_cache.db")
    if os.path.exists(p1):
        return p1
    # tmdb_agent 用户的常见位置
    candidates = [
        os.path.expanduser(r"~\Documents\tmdb_agent\cache\tmdb_cache.db"),
        os.path.expanduser(r"~\Documents\tmdb\cache\tmdb_cache.db"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return p1  # 返回第一个，调用方会判断是否存在


def main():
    print("=" * 60, flush=True)
    print("tmdb 缓存诊断 v23.55", flush=True)
    print("=" * 60, flush=True)

    if len(sys.argv) >= 2:
        db = sys.argv[1]
    else:
        db = find_default_db()
    print(f"DB 路径: {db}", flush=True)
    print(f"DB 存在: {os.path.exists(db)}", flush=True)
    if not os.path.exists(db):
        print("!!! db 文件不存在 !!!", flush=True)
        print("请把 db 完整路径作为参数传入:", flush=True)
        print(f'  python "{__file__}" "C:\\你的\\路径\\tmdb_cache.db"', flush=True)
        return 1
    print(f"DB 大小: {os.path.getsize(db) / 1024 / 1024:.1f} MB", flush=True)
    print(flush=True)

    try:
        conn = sqlite3.connect(db, timeout=10)
        conn.row_factory = sqlite3.Row
    except Exception as e:
        print(f"!! 连接 db 失败: {e}", flush=True)
        return 1

    # 1) 表结构
    print("【1】表结构 (movies)", flush=True)
    rows = list(conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='movies'"))
    if rows:
        for r in rows:
            print(r["sql"], flush=True)
    else:
        print("  !! 找不到 movies 表 !!", flush=True)
    print(flush=True)

    # 2) 索引
    print("【2】索引 (movies 表)", flush=True)
    idx_rows = list(conn.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='movies' ORDER BY name"))
    if idx_rows:
        for r in idx_rows:
            print(f"  {r['name']}: {r['sql']}", flush=True)
    else:
        print("  !! 没有任何索引 !!", flush=True)
    print(flush=True)

    # 3) 总数
    try:
        n = conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
    except Exception as e:
        print(f"!! 统计行数失败: {e}", flush=True)
        n = 0
    print(f"【3】总条数: {n:,}", flush=True)
    print(flush=True)

    # 4) search_key 样本
    print("【4】search_key 样本 (前 10 条非空)", flush=True)
    try:
        for r in conn.execute("SELECT id, search_key, title_en, year FROM movies WHERE search_key != '' LIMIT 10"):
            print(f"  id={r['id']:>8}  sk='{r['search_key']}'  en='{(r['title_en'] or '')[:40]}'  yr={r['year']}", flush=True)
    except Exception as e:
        print(f"  !! {e}", flush=True)
    print(flush=True)

    # 5) search_key 异常
    print("【5】search_key 异常统计", flush=True)
    try:
        empty = conn.execute("SELECT COUNT(*) FROM movies WHERE search_key IS NULL OR search_key = ''").fetchone()[0]
        no_pipe = conn.execute("SELECT COUNT(*) FROM movies WHERE search_key NOT LIKE '%|%'").fetchone()[0]
        print(f"  空/NULL: {empty:,}", flush=True)
        print(f"  无年份分隔符(|): {no_pipe:,}", flush=True)
    except Exception as e:
        print(f"  !! {e}", flush=True)
    print(flush=True)

    # 6) raw_json 解析失败率
    print("【6】raw_json 解析失败率 (抽样 1000)", flush=True)
    try:
        bad = 0
        sample = list(conn.execute("SELECT raw_json FROM movies WHERE raw_json != '' LIMIT 1000"))
        for (raw,) in sample:
            try:
                import json
                json.loads(raw)
            except Exception:
                bad += 1
        print(f"  解析失败: {bad}/1000", flush=True)
    except Exception as e:
        print(f"  !! {e}", flush=True)
    print(flush=True)

    # 7) search_broad 实测
    print("【7】search_broad 实测 (TmdbCache.search_broad)", flush=True)
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from core.tmdb_cache import TmdbCache
        cache = TmdbCache(db)
        for q in ["casino", "angel", "the matrix"]:
            t0 = time.time()
            try:
                r = cache.search_broad(q)
                dt = time.time() - t0
                print(f"  search_broad('{q}'): total={r['total']:,} 耗时={dt:.3f}s 异常=无", flush=True)
            except Exception as e:
                dt = time.time() - t0
                print(f"  search_broad('{q}'): 异常 {type(e).__name__}: {e} 耗时={dt:.3f}s", flush=True)
                traceback.print_exc()
    except Exception as e:
        print(f"  !! 无法导入 TmdbCache: {e}", flush=True)
        traceback.print_exc()
    print(flush=True)

    # 8) EXPLAIN QUERY PLAN
    print("【8】EXPLAIN QUERY PLAN (验证 search_key 索引是否被使用)", flush=True)
    for q in ["casino", "angel"]:
        try:
            sql = "SELECT id, search_key FROM movies WHERE search_key LIKE ? OR search_key LIKE ? LIMIT 5"
            plan = list(conn.execute(f"EXPLAIN QUERY PLAN {sql}", (q + "|%", "%" + q + "%")))
            for p in plan:
                # 关键: 看 detail 里有没有 "USING INDEX"
                detail = p["detail"] if "detail" in p.keys() else str(dict(p))
                print(f"  '{q}': {detail}", flush=True)
        except Exception as e:
            print(f"  '{q}': !! {e}", flush=True)
    print(flush=True)

    print("诊断完成。", flush=True)
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        traceback.print_exc()
        rc = 1
    # 让 cmd 窗口停留一下看到结果
    try:
        input("\n按回车退出...")
    except Exception:
        pass
    sys.exit(rc)
