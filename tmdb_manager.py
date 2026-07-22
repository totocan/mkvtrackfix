# -*- coding: utf-8 -*-
"""
TMDB 缓存管理器 — 独立 GUI 界面

功能：
  - 查看缓存统计（总条数、含中文名数、按来源分布）
  - 导入 Kaggle CSV 数据集（全量初始化）
  - 扫描目录并逐条预拉取（可后台挂机）
  - 手动开关缓存（影响主程序是否走本地查）

用法：
  双击 tmdb_manager.bat 启动
"""
import os
import sys
import time
import threading
import json

_APP_ROOT = os.path.dirname(os.path.abspath(__file__))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QFileDialog, QGroupBox,
    QLineEdit, QCheckBox, QSpinBox, QProgressBar, QMessageBox,
    QTabWidget, QFormLayout, QFrame, QSplitter,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont


class ImportWorker(QThread):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(int)
    log = pyqtSignal(str)

    def __init__(self, csv_path):
        super().__init__()
        self.csv_path = csv_path

    def run(self):
        from core.tmdb_cache import TmdbCache
        cache = TmdbCache()
        self.log.emit(f"开始导入: {self.csv_path}")
        ts = time.time()
        total = cache.import_kaggle_csv(self.csv_path,
                                        callback=lambda c, t: self.progress.emit(c, 0))
        elapsed = time.time() - ts
        self.log.emit(f"导入完成: {total} 条记录, 耗时 {elapsed:.0f} 秒")
        self.finished.emit(total)


class ScanWorker(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(int)

    def __init__(self, directory, recursive, interval):
        super().__init__()
        self.directory = directory
        self.recursive = recursive
        self.interval = interval
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        from core.tmdb_cache import TmdbCache
        import requests
        import re
        cache = TmdbCache()
        processed = set()
        count = 0
        while not self._stop:
            files = []
            if self.recursive:
                for root, dirs, fnames in os.walk(self.directory):
                    for f in fnames:
                        if f.lower().endswith(('.mkv', '.mp4')):
                            files.append(os.path.join(root, f))
            else:
                for f in os.listdir(self.directory):
                    if f.lower().endswith(('.mkv', '.mp4')):
                        files.append(os.path.join(self.directory, f))
            new_files = [f for f in files if f not in processed]
            for f in new_files:
                if self._stop:
                    return
                title, year = self._extract_info(f)
                if not title:
                    processed.add(f)
                    continue
                cached = cache.lookup(title, year)
                if cached:
                    processed.add(f)
                    continue
                self.log.emit(f"查: {title} ({year})...")
                result = self._query_tmdb(title, year)
                if result:
                    cache.save(title, year, result)
                    self.log.emit(f"  ✓ 已缓存")
                    count += 1
                else:
                    self.log.emit(f"  ✗ 无结果")
                processed.add(f)
                time.sleep(1.5)
            if not self.interval:
                break
            for _ in range(self.interval * 60):
                if self._stop:
                    return
                time.sleep(1)
        self.done.emit(count)

    def _extract_info(self, path):
        base = os.path.splitext(os.path.basename(path))[0]
        m = re.search(r'[.\(]\s*(\d{4})\s*[.\)]', base)
        year = int(m.group(1)) if m else None
        if m:
            title = base[:m.start()].replace('.', ' ').replace('_', ' ').strip()
        else:
            title = base.replace('.', ' ').replace('_', ' ').strip()
        for suffix in ['bluray', 'web dl', 'webrip', 'hdrip', 'x264', 'x265',
                       'h264', 'h265', '10bit', '2audio', 'remux', '2160p',
                       '1080p', '720p', 'dts', 'ac3', 'aac', 'flac']:
            title = re.sub(r'\b' + suffix + r'\b', '', title, flags=re.IGNORECASE)
        title = ' '.join(title.split()).strip()
        return title, year

    def _query_tmdb(self, title, year):
        try:
            import requests
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept-Language': 'zh-CN,zh;q=0.9',
            }
            url = f"https://www.themoviedb.org/search?query={requests.utils.quote(title)}"
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                return None
            mids = re.findall(r'/movie/(\d+)', resp.text)
            if not mids:
                return None
            movie_id = mids[0]
            url2 = f"https://www.themoviedb.org/movie/{movie_id}"
            resp2 = requests.get(url2, headers=headers, timeout=15)
            if resp2.status_code != 200:
                return None
            text = resp2.text
            country = re.search(r'data-country-code="([^"]+)"', text)
            lang = re.search(r'data-original-language="([^"]+)"', text)
            title_zh_m = re.search(r'class="title"[^>]*>([^<]+)<', text)
            return {
                "title_en": title, "title_zh": title_zh_m.group(1).strip() if title_zh_m else "",
                "country": country.group(1) if country else "",
                "language": lang.group(1) if lang else "",
                "tmdb_id": int(movie_id),
                "source": "tmdb",
            }
        except Exception:
            return None


class TmdbManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TMDB 缓存管理器")
        self.scan_worker = None
        self._init_ui()

    def _init_ui(self):
        # v23.52: 字号与主程序设置联动（直接读 config.json，避免触发 logger 初始化）
        size, family = 10, "Microsoft YaHei UI"
        try:
            _cfg_path = os.path.join(_APP_ROOT, "config.json")
            if os.path.exists(_cfg_path):
                with open(_cfg_path, "r", encoding="utf-8") as _f:
                    _d = json.load(_f)
                size = int(_d.get("gui_font_size", 10))
                family = _d.get("gui_font_family", "") or "Microsoft YaHei UI"
        except Exception:
            pass
        self._ui_font = QFont(family, size)
        self._mono_font = QFont("Consolas", max(9, size - 1))
        self.setFont(self._ui_font)

        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        # ===== 标签页1: 概览 =====
        tab_overview = QWidget()
        vl = QVBoxLayout(tab_overview)
        self.lbl_stats = QLabel("点击「刷新统计」查看缓存状态")
        self.lbl_stats.setFont(self._mono_font)
        self.lbl_stats.setWordWrap(True)
        vl.addWidget(self.lbl_stats)
        btn_refresh = QPushButton("🔄 刷新统计")
        btn_refresh.clicked.connect(self._refresh_stats)
        vl.addWidget(btn_refresh)
        vl.addStretch()
        tabs.addTab(tab_overview, "概览")

        # ===== 标签页2: 初始化 =====
        tab_init = QWidget()
        vl2 = QVBoxLayout(tab_init)
        link = QLabel(
            '<a href="https://www.kaggle.com/datasets/alanvourch/tmdb-movies-daily-updates">'
            '📥 打开 Kaggle 数据集下载页面</a><br>'
            '<span style="color:#888;font-size:9pt;">约 772MB (CSV)，每日更新，内含 96 万+ 电影元数据</span>')
        link.setOpenExternalLinks(True)
        link.setWordWrap(True)
        link.setStyleSheet("font-size:12pt; padding:8px;")
        vl2.addWidget(link)
        vl2.addWidget(info)
        btn_sel = QPushButton("📁 选择 CSV 文件并导入")
        btn_sel.clicked.connect(self._import_csv)
        vl2.addWidget(btn_sel)
        self.progress_bar = QProgressBar()
        vl2.addWidget(self.progress_bar)
        tabs.addTab(tab_init, "初始化")

        # ===== 标签页3: 预拉取 =====
        tab_scan = QWidget()
        vl3 = QVBoxLayout(tab_scan)
        f3 = QFormLayout()
        self.le_dir = QLineEdit()
        self.le_dir.setPlaceholderText(r"\\NAS\影视\电影 或 D:\Movies")
        btn_browse = QPushButton("浏览...")
        btn_browse.clicked.connect(lambda: self.le_dir.setText(
            QFileDialog.getExistingDirectory(self, "选择电影目录")))
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.le_dir, 1)
        dir_row.addWidget(btn_browse)
        f3.addRow("目录:", dir_row)
        self.cb_recursive = QCheckBox("递归子目录")
        self.cb_recursive.setChecked(True)
        f3.addRow(self.cb_recursive)
        self.sp_interval = QSpinBox()
        self.sp_interval.setRange(0, 999)
        self.sp_interval.setValue(30)
        self.sp_interval.setSuffix(" 分钟(0=只扫一次)")
        f3.addRow("后台间隔:", self.sp_interval)
        vl3.addLayout(f3)
        hb = QHBoxLayout()
        self.btn_start_scan = QPushButton("▶ 开始预拉取")
        self.btn_start_scan.clicked.connect(self._start_scan)
        self.btn_stop_scan = QPushButton("⏹ 停止")
        self.btn_stop_scan.clicked.connect(self._stop_scan)
        self.btn_stop_scan.setEnabled(False)
        hb.addWidget(self.btn_start_scan)
        hb.addWidget(self.btn_stop_scan)
        vl3.addLayout(hb)
        tabs.addTab(tab_scan, "预拉取")

        # ===== 日志 =====
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(self._mono_font)
        self.log.setMinimumHeight(200)
        tabs.addTab(self.log, "日志")

        self._refresh_stats()

    def _log(self, msg):
        self.log.append(msg)

    def _refresh_stats(self):
        try:
            from core.tmdb_cache import TmdbCache
            cache = TmdbCache()
            stats = cache.stats()
            db_path = os.path.join(os.path.dirname(cache.db_path), "tmdb_cache.db")
            size = os.path.getsize(db_path) // 1024 // 1024 if os.path.exists(db_path) else 0
            self.lbl_stats.setText(
                f"📊 TMDB 缓存统计\n"
                f"{'─' * 40}\n"
                f"数据库路径: {db_path}\n"
                f"数据库大小: {size} MB\n"
                f"总条数:     {stats['total']:,}\n"
                f"含中文名:   {stats['with_chinese_title']:,}\n"
                f"按来源分布:\n" +
                "\n".join(f"  {src}: {cnt:,}" for src, cnt in stats['by_source'].items())
            )
        except Exception as e:
            self.lbl_stats.setText(f"⚠ 读取缓存失败: {e}")

    def _import_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Kaggle CSV", "", "CSV (*.csv);;所有文件 (*.*)")
        if not path:
            return
        self.worker = ImportWorker(path)
        self.worker.log.connect(self._log)
        self.worker.progress.connect(lambda c, t: self.progress_bar.setValue(c % 100))
        self.worker.finished.connect(lambda n: QMessageBox.information(
            self, "导入完成", f"成功导入 {n:,} 条记录"))
        self.worker.finished.connect(self._refresh_stats)
        self.worker.start()

    def _start_scan(self):
        directory = self.le_dir.text().strip()
        if not directory or not os.path.isdir(directory):
            QMessageBox.warning(self, "提示", "请选择有效的电影目录")
            return
        self.scan_worker = ScanWorker(
            directory, self.cb_recursive.isChecked(), self.sp_interval.value())
        self.scan_worker.log.connect(self._log)
        self.scan_worker.done.connect(lambda n: self._log(f"共缓存 {n} 部电影"))
        self.scan_worker.done.connect(self._refresh_stats)
        self.scan_worker.start()
        self.btn_start_scan.setEnabled(False)
        self.btn_stop_scan.setEnabled(True)

    def _stop_scan(self):
        if self.scan_worker:
            self.scan_worker.stop()
            self._log("已发送停止信号，等待当前查询完成...")
        self.btn_start_scan.setEnabled(True)
        self.btn_stop_scan.setEnabled(False)


def main():
    # v23.53: 全局异常捕获，崩溃时写日志方便排查
    try:
        app = QApplication(sys.argv)
        # v23.52: Qt 中文翻译（和主程序保持一致）
        from PyQt5.QtCore import QTranslator, QLocale, QLibraryInfo
        _tr = QTranslator()
        lp = QLibraryInfo.location(QLibraryInfo.TranslationsPath)
        if _tr.load(QLocale.system(), 'qt', '_', lp):
            app.installTranslator(_tr)
        w = TmdbManager()
        w.show()
        w.showMaximized()
        sys.exit(app.exec_())
    except Exception as _e:
        import traceback
        err_log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "tmdb_crash.log")
        os.makedirs(os.path.dirname(err_log), exist_ok=True)
        with open(err_log, "w", encoding="utf-8") as _f:
            traceback.print_exc(file=_f)
        raise  # 让调用者也能看到


if __name__ == "__main__":
    main()
