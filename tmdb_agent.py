# -*- coding: utf-8 -*-
"""
TMDB 预缓存 Agent — 独立子程序

批量将电影目录中的文件提取名称 → 查 TMDB → 入库。
可 7x24 小时挂机，逐条查询间隔 1~2 秒，避免触发反爬。

用法:
    # 查单个目录
    python tmdb_agent.py --dir "D:\Movies" --recursive
    
    # 导入 Kaggle CSV 数据集（约 772MB，首次运行）
    python tmdb_agent.py --import-kaggle TMDB_all_movies.csv
    
    # 查看缓存统计
    python tmdb_agent.py --stats
    
    # 连续后台运行（每 30 分钟扫描一次新文件）
    python tmdb_agent.py --dir "\\NAS\Movies" --daemon --interval 30
"""
import os
import sys
import time
import argparse
import json
import re
import datetime

# 将项目根目录加入 Python 路径
_APP_ROOT = os.path.dirname(os.path.abspath(__file__))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)


def extract_movie_info(path):
    """从文件路径/文件名中提取英文标题和年份"""
    base = os.path.splitext(os.path.basename(path))[0]
    # 尝试匹配 "Title.Year.xxx" 或 "Title (Year) xxx"
    m = re.search(r'[.\(]\s*(\d{4})\s*[.\)]', base)
    year = int(m.group(1)) if m else None
    # 提取标题部分（年份之前）
    if m:
        title = base[:m.start()].replace('.', ' ').replace('_', ' ').strip()
    else:
        title = base.replace('.', ' ').replace('_', ' ').strip()
    # 去除常见后缀词
    for suffix in ['bluray', 'web dl', 'webrip', 'hdrip', 'x264', 'x265',
                   'h264', 'h265', '10bit', '2audio', 'remux', '2160p',
                   '1080p', '720p', 'dts', 'ac3', 'aac', 'flac']:
        title = re.sub(r'\b' + suffix + r'\b', '', title, flags=re.IGNORECASE)
    title = ' '.join(title.split()).strip()
    return title, year


def scan_directory(directory, recursive=True, exts=('.mkv', '.mp4')):
    """扫描目录获取视频文件列表"""
    files = []
    if recursive:
        for root, dirs, fnames in os.walk(directory):
            for f in fnames:
                if f.lower().endswith(exts):
                    files.append(os.path.join(root, f))
    else:
        for f in os.listdir(directory):
            if f.lower().endswith(exts):
                files.append(os.path.join(directory, f))
    return files


def query_tmdb_online(title, year, api_key=None):
    """在线查询 TMDB（与 douban.py 相同的爬取逻辑）"""
    import requests
    from bs4 import BeautifulSoup
    headers = {
        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/125.0.0.0 Safari/537.36'),
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    # 搜索
    search_url = f"https://www.themoviedb.org/search?query={requests.utils.quote(title)}"
    try:
        resp = requests.get(search_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None
        # 提取 movie_id
        mids = re.findall(r'/movie/(\d+)', resp.text)
        if not mids:
            return None
        movie_id = mids[0]
        # 获取详情
        detail_url = f"https://www.themoviedb.org/movie/{movie_id}"
        resp2 = requests.get(detail_url, headers=headers, timeout=15)
        if resp2.status_code != 200:
            return None
        text = resp2.text
        # 提取国家
        country = ""
        cm = re.search(r'data-country-code="([^"]+)"', text)
        if cm:
            country = cm.group(1)
        # 提取语言
        lang = ""
        lm = re.search(r'data-original-language="([^"]+)"', text)
        if lm:
            lang = lm.group(1)
        # 提取中文名
        title_zh = ""
        # TMDB zh-CN 页面有单独的 h2, 或者 title 标签
        tm = re.search(r'<h2[^>]*>([^<]+)</h2>', text)
        if tm:
            title_zh = tm.group(1).strip()
        return {
            "title_en": title,
            "title_zh": title_zh,
            "country": country,
            "language": lang,
            "tmdb_id": int(movie_id) if movie_id.isdigit() else None,
            "source": "tmdb",
        }
    except Exception as e:
        print(f"  TMDB 查询异常: {e}")
        return None


def cmd_import_kaggle(csv_path):
    """导入 Kaggle CSV 数据集"""
    from core.tmdb_cache import TmdbCache
    cache = TmdbCache()
    ts = time.time()
    print(f"开始导入: {csv_path}")
    total = cache.import_kaggle_csv(csv_path)
    elapsed = time.time() - ts
    print(f"导入完成: {total} 条记录, 耗时 {elapsed:.0f} 秒")
    print(f"缓存统计: {cache.stats()}")


def cmd_scan_dir(directory, recursive, daemon=False, interval=30):
    """扫描目录并逐条查 TMDB 入库"""
    from core.tmdb_cache import TmdbCache
    cache = TmdbCache()
    processed = set()
    while True:
        files = scan_directory(directory, recursive)
        new_files = [f for f in files if f not in processed]
        if new_files:
            print(f"发现 {len(new_files)} 个新文件")
            for f in new_files:
                title, year = extract_movie_info(f)
                if not title:
                    continue
                # 查缓存是否已有
                cached = cache.lookup(title, year)
                if cached:
                    processed.add(f)
                    continue
                print(f"查 TMDB: {title} ({year})", end="")
                result = query_tmdb_online(title, year)
                if result:
                    cache.save(title, year, result)
                    print(f" → {'✓' if result.get('country') else '✗'}")
                else:
                    print(" → TMDB 无结果")
                processed.add(f)
                time.sleep(1.5)  # 避免反爬
        if not daemon:
            break
        print(f"等待 {interval} 分钟后再次扫描...")
        time.sleep(interval * 60)


def cmd_stats():
    """查看缓存统计"""
    from core.tmdb_cache import TmdbCache
    cache = TmdbCache()
    stats = cache.stats()
    print(f"缓存统计:")
    print(f"  总条数: {stats['total']}")
    print(f"  含中文名: {stats['with_chinese_title']}")
    print(f"  按来源:")
    for src, cnt in stats['by_source'].items():
        print(f"    {src}: {cnt}")
    cache.close()


def main():
    parser = argparse.ArgumentParser(description="TMDB 预缓存 Agent")
    parser.add_argument("--dir", help="扫描目录")
    parser.add_argument("--import-kaggle", metavar="CSV", help="导入 Kaggle CSV 数据集")
    parser.add_argument("--recursive", action="store_true", help="递归扫描子目录")
    parser.add_argument("--stats", action="store_true", help="查看缓存统计")
    parser.add_argument("--daemon", action="store_true", help="后台模式")
    parser.add_argument("--interval", type=int, default=30, help="后台扫描间隔(分钟)")
    args = parser.parse_args()

    if args.stats:
        cmd_stats()
    elif args.import_kaggle:
        cmd_import_kaggle(args.import_kaggle)
    elif args.dir:
        cmd_scan_dir(args.dir, args.recursive, args.daemon, args.interval)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
