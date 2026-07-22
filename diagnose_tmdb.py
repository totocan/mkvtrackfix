# -*- coding: utf-8 -*-
"""
tmdb 缓存诊断脚本 (v23.55 零依赖版)
只用标准库 sqlite3，不 import 任何项目模块。
用法:
  python diagnose_tmdb.py
  python diagnose_tmdb.py "C:\path\to\tmdb_cache.db"
"""
import os
import sys
import time


def main():
    print("=" * 60)
    print("tmdb 缓存诊断 v23.55 (零依赖版)")
    print("=" * 60)

    # 找 db
    if len(sys.argv) >= 2:
        db = sys.argv[1]
    else:
        candidates = [
            os.path.join(os.getcwd(), "cache", "tmdb_cache.db"),
            os.path.expanduser(r"~\Documents\tmdb_agent\cache\tmdb_cache.db"),
            os.path.expanduser(r"~\Documents\tmdb\cache\tmdb_cache.db"),
        ]
        db = next((c for c in candidates if os.path.exists(c)), candidates[0])

    print(f"DB 路径: {db}")
    print(f"DB 存在: {os.path.exists(db)}")
    if not os.path.exists(db):
        print("!!! db 文件不存在 !!!")
        print("请用以下方式指定 db 路径:")
        print(f'  python "{__file__}" "C:\\你的完整路径\\tmdb_cache.db"')
        return 1
    print(f"DB 大小: {os.path.getsize(db) / 1024 / 1024:.1f} MB")
    print()

    import sqlite3
    conn = sqlite3.connect(db, timeout=10)
    conn.row_factory = sqlite3.Row

    # 1) 表结构
    print("【1】表结构 (movies)")
    rows = list(conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='movies'"))
    if rows:
        for r in rows:
            print(r["sql"])
    else:
        print("  !! 找不到 movies 表 !!")
    print()

    # 2) 索引
    print("【2】索引 (movies 表)")
    idx = list(conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='movies' ORDER BY name"))
    if idx:
        for r in idx:
            print(f"  {r['name']}: {r['sql']}")
    else:
        print("  !! 没有任何索引 !!")
    print()

    # 3) 总数
    try:
        n = conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
    except Exception as e:
        print(f"!! 统计行数失败: {e}")
        n = 0
    print(f"【3】总条数: {n:,}")
    print()

    # 4) search_key 样本
    print("【4】search_key 样本 (前 10 条)")
    try:
        for r in conn.execute(
                "SELECT id, search_key, title_en, year FROM movies WHERE search_key != '' LIMIT 10"):
            print(f"  id={r['id']:>8}  sk='{r['search_key']}'  en='{(r['title_en'] or '')[:40]}'  yr={r['year']}")
    except Exception as e:
        print(f"  !! {e}")
    print()

    # 5) 异常统计
    print("【5】search_key 异常统计")
    try:
        empty = conn.execute("SELECT COUNT(*) FROM movies WHERE search_key IS NULL OR search_key = ''").fetchone()[0]
        no_pipe = conn.execute("SELECT COUNT(*) FROM movies WHERE search_key NOT LIKE '%|%'").fetchone()[0]
        print(f"  空/NULL: {empty:,}")
        print(f"  无 | 分隔符: {no_pipe:,}")
    except Exception as e:
        print(f"  !! {e}")
    print()

    # 6) raw_json 解析
    print("【6】raw_json 解析失败率 (抽样 1000)")
    try:
        bad = 0
        import json
        for (raw,) in conn.execute("SELECT raw_json FROM movies WHERE raw_json != '' LIMIT 1000"):
            try:
                json.loads(raw)
            except Exception:
                bad += 1
        print(f"  解析失败: {bad}/1000")
    except Exception as e:
        print(f"  !! {e}")
    print()

    # 7) SQL 实测搜索耗时（直接用 SQL，不走项目代码）
    print("【7】SQL 实测搜索 (模拟泛搜索的 LIKE 模式)")
    for q in ["casino", "angel", "matrix"]:
        t0 = time.time()
        try:
            rows = list(conn.execute(
                "SELECT id, search_key, title_en, year FROM movies "
                "WHERE search_key LIKE ? OR search_key LIKE ? LIMIT 5",
                (q + "|%", "%" + q + "%")))
            dt = time.time() - t0
            print(f"  '{q}': 返回 {len(rows)} 条 耗时 {dt:.3f}s")
            for r in rows[:3]:
                print(f"    id={r['id']}  sk='{r['search_key']}'  en='{(r['title_en'] or '')[:30]}'")
        except Exception as e:
            print(f"  '{q}': 异常 {e}")
    print()

    # 8) EXPLAIN 验证索引
    print("【8】EXPLAIN QUERY PLAN (看 search_key 索引是否被命中)")
    for q in ["casino", "angel"]:
        try:
            sql = ("EXPLAIN QUERY PLAN SELECT id FROM movies "
                   "WHERE search_key LIKE ? OR search_key LIKE ? LIMIT 5")
            plan = list(conn.execute(sql, (q + "|%", "%" + q + "%")))
            for p in plan:
                detail = p["detail"] if "detail" in p.keys() else str(dict(p))
                hit = "✓ 命中索引" if "USING INDEX" in detail else "✗ 全表扫描"
                print(f"  '{q}': {hit}  {detail}")
        except Exception as e:
            print(f"  '{q}': {e}")
    print()

    print("诊断完成。")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        import traceback
        traceback.print_exc()
        rc = 1
    try:
        input("\n按回车退出...")
    except Exception:
        pass
    sys.exit(rc)
