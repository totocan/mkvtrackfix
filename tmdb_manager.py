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
    QTabWidget, QFormLayout, QFrame, QSplitter, QComboBox,
    QTableWidget, QTableWidgetItem,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont


def _cfg_path():
    return os.path.join(_APP_ROOT, "config.json")


def load_config():
    try:
        if os.path.exists(_cfg_path()):
            with open(_cfg_path(), "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_config_key(key, value):
    d = load_config()
    d[key] = value
    try:
        with open(_cfg_path(), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


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


class StrengthenWorker(QThread):
    """自动强化：TMDB API 批量补 title_zh + country_name（v23.54 新增）。"""
    log = pyqtSignal(str)
    progress = pyqtSignal(int, int, int)  # processed, total, updated
    done = pyqtSignal(int, int)

    def __init__(self, api_key, interval, batch_limit=0, start_after_id=0):
        super().__init__()
        self.api_key = api_key
        self.interval = interval
        self.batch_limit = batch_limit
        self.start_after_id = start_after_id
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        from core.tmdb_cache import TmdbCache, CACHE_DIR
        import json
        cache = TmdbCache()
        state_path = os.path.join(CACHE_DIR, "strengthen_resume.json")
        try:
            processed, updated, last_id = cache.strengthen_missing(
                self.api_key, interval=self.interval,
                stop_check=lambda: self._stop,
                on_log=lambda m: self.log.emit(m),
                on_progress=lambda p, t, u: self.progress.emit(p, t, u),
                batch_limit=self.batch_limit,
                start_after_id=self.start_after_id)
            # 落盘续跑点（停止或完成都记录）
            try:
                with open(state_path, "w", encoding="utf-8") as f:
                    json.dump({"last_id": last_id}, f)
            except Exception:
                pass
            self.done.emit(processed, updated)
        except Exception as e:
            self.log.emit(f"强化异常: {e}")


class ConvertCountryWorker(QThread):
    """🌐 转中文国名（本地零成本，后台线程避免大库卡 UI）。"""
    log = pyqtSignal(str)
    done = pyqtSignal(int)

    def __init__(self):
        super().__init__()

    def run(self):
        from core.tmdb_cache import TmdbCache, COUNTRY_MAP
        try:
            cache = TmdbCache()
            self.log.emit("🌐 转中文国名（写到【国家（修订）】列）...")
            n, unmatched = cache.apply_country_names()
            self.log.emit(f"✅ 已写入【国家（修订）】{n:,} 条")
            if unmatched:
                self.log.emit("   未匹配的值 TOP5（这些 ISO 国家码/语言码不在映射表里）：")
                for val, cnt in unmatched:
                    self.log.emit(f"     · '{val}': {cnt} 行")
            # 诊断：解释「应该能填但没填上」的原因
            try:
                conn = cache._get_conn()
                total_match = conn.execute(
                    "SELECT COUNT(*) FROM movies "
                    "WHERE (IFNULL(country,'') != '' OR IFNULL(language,'') != '') "
                    "AND IFNULL(country_revised,'') = ''"
                ).fetchone()[0]
                if total_match == 0:
                    self.log.emit("   诊断：所有 country/language 已成功写入【国家（修订）】")
                elif n == 0:
                    self.log.emit(f"   诊断：仍待补 {total_match:,} 行（country/language 都不在映射表里）")
                    if COUNTRY_MAP:
                        placeholders = ",".join("?" * len(COUNTRY_MAP))
                        cur = conn.execute(
                            f"SELECT country, COUNT(*) c FROM movies "
                            f"WHERE IFNULL(country_name,'') = '' "
                            f"AND IFNULL(country,'') != '' "
                            f"AND country NOT IN ({placeholders}) "
                            f"GROUP BY country ORDER BY c DESC LIMIT 5",
                            list(COUNTRY_MAP.keys()),
                        )
                        rows = cur.fetchall()
                        if rows:
                            self.log.emit("   不在 COUNTRY_MAP 的 ISO 码 TOP5：")
                            for code, c in rows:
                                self.log.emit(f"     · {code}: {c:,} 行")
                        else:
                            self.log.emit("   所有待补 ISO 码都在 COUNTRY_MAP 内（异常，请检查 COUNTRY_MAP）")
                    self.log.emit("   如需补全，编辑 core/tmdb_cache.py 的 COUNTRY_MAP 加码")
            except Exception as e:
                self.log.emit(f"   诊断查询失败: {e}")
            self.done.emit(n)
        except Exception as e:
            self.log.emit(f"转国名异常: {e}")


class BackfillCountryWorker(QThread):
    """🩹 从 raw_json 反补 country / country_name（修 Kaggle 旧数据）。"""
    log = pyqtSignal(str)
    done = pyqtSignal(int)

    def run(self):
        from core.tmdb_cache import TmdbCache
        try:
            cache = TmdbCache()
            self.log.emit("🩹 开始从 raw_json 反补国名（Kaggle 旧数据）...")
            n = cache.backfill_country_from_raw_json()
            self.log.emit(f"✅ 已反补 country_name {n:,} 条")
            self.done.emit(n)
        except Exception as e:
            self.log.emit(f"反补国名异常: {e}")



class IndexBuildWorker(QThread):
    """手动（重建）索引，后台执行避免大库卡 UI。"""
    progress = pyqtSignal(int, int, str)  # step, total, phase_name
    log = pyqtSignal(str)
    done = pyqtSignal(str)

    def run(self):
        from core.tmdb_cache import TmdbCache
        try:
            cache = TmdbCache()
            cache.build_search_index(
                on_progress=lambda s, t, p: self.progress.emit(s, t, p),
                on_log=lambda m: self.log.emit(m))
            st = cache.index_status()
            self.done.emit(
                f"✅ 索引构建完成：搜索索引{'已建' if st['has_search_index'] else '缺失'}，"
                f"数据行数 {st['row_count']:,}")
        except Exception as e:
            self.log.emit(f"索引构建异常: {e}")
            self.done.emit(f"⚠ 索引构建失败: {e}")


class DbBrowseWorker(QThread):
    """数据浏览：分页查询 movies 表（后台避免大库卡 UI）。"""
    result = pyqtSignal(list, int, int, int)  # rows, total, page, page_size
    log = pyqtSignal(str)

    def __init__(self, filters, page, page_size=200):
        super().__init__()
        self.filters = filters
        self.page = page
        self.page_size = page_size

    def run(self):
        from core.tmdb_cache import TmdbCache
        try:
            cache = TmdbCache()
            rows, total = cache.browse_rows(self.filters, self.page, self.page_size)
            self.result.emit(rows, total, self.page, self.page_size)
        except Exception as e:
            self.log.emit(f"浏览查询失败: {e}")
            self.result.emit([], 0, self.page, self.page_size)


class DbExportWorker(QThread):
    """数据浏览：将筛选结果导出 CSV（后台流式写入，带进度）。"""
    progress = pyqtSignal(int, int)  # written, total
    log = pyqtSignal(str)
    done = pyqtSignal(int, str)       # written, path

    def __init__(self, filters, csv_path):
        super().__init__()
        self.filters = filters
        self.csv_path = csv_path

    def run(self):
        from core.tmdb_cache import TmdbCache
        try:
            cache = TmdbCache()
            n = cache.export_rows(
                self.filters, self.csv_path,
                callback=lambda w, t: self.progress.emit(w, t))
            self.done.emit(n, self.csv_path)
        except Exception as e:
            self.log.emit(f"导出失败: {e}")
            self.done.emit(0, self.csv_path)


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

        # ===== 标签页1: 概览 + 初始化（合并） =====
        tab_overview = QWidget()
        vl = QVBoxLayout(tab_overview)
        # 上区：统计概览
        self.lbl_stats = QLabel("点击「刷新统计」查看缓存状态")
        self.lbl_stats.setFont(self._mono_font)
        self.lbl_stats.setWordWrap(True)
        vl.addWidget(self.lbl_stats)
        hb_top = QHBoxLayout()
        btn_refresh = QPushButton("🔄 刷新统计")
        btn_refresh.clicked.connect(self._refresh_stats)
        hb_top.addWidget(btn_refresh)
        hb_top.addStretch()
        vl.addLayout(hb_top)
        # 分隔
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        vl.addWidget(line)
        # 下区：CSV 导入初始化
        link = QLabel(
            '<a href="https://www.kaggle.com/datasets/alanvourch/tmdb-movies-daily-updates">'
            '📥 打开 Kaggle 数据集下载页面</a><br>'
            '<span style="color:#888;font-size:9pt;">约 772MB (CSV)，每日更新，内含 96 万+ 电影元数据</span>')
        link.setOpenExternalLinks(True)
        link.setWordWrap(True)
        link.setStyleSheet("font-size:12pt; padding:8px;")
        vl.addWidget(link)
        btn_sel = QPushButton("📁 选择 CSV 文件并导入")
        btn_sel.clicked.connect(self._import_csv)
        vl.addWidget(btn_sel)
        self.progress_bar = QProgressBar()
        vl.addWidget(self.progress_bar)
        vl.addStretch()
        tabs.addTab(tab_overview, "概览 / 初始化")

        # ===== 标签页5: 自动强化 =====
        tab_str = QWidget()
        xl = QVBoxLayout(tab_str)
        xf = QFormLayout()
        self.le_apikey = QLineEdit()
        self.le_apikey.setEchoMode(QLineEdit.Password)
        self.le_apikey.setPlaceholderText("TMDB API Key (v3 auth)")
        _cfg = load_config()
        if _cfg.get("tmdb_api_key"):
            self.le_apikey.setText(_cfg["tmdb_api_key"])
        xf.addRow("API Key:", self.le_apikey)
        self.btn_save_key = QPushButton("💾 保存 Key")
        self.btn_save_key.clicked.connect(self._save_apikey)
        xf.addRow(self.btn_save_key)
        self.cb_interval = QComboBox()
        # (显示文本, 每条间隔秒数) —— 小数=高速档(条/秒)，整数=秒/条
        _speeds = [
            ("1秒 50 条", 0.02), ("1秒 30 条", 0.033), ("1秒 20 条", 0.05),
            ("1秒 10 条", 0.1), ("1秒 1 条", 1.0),
            ("5秒 1 条", 5.0), ("10秒 1 条", 10.0), ("15秒 1 条", 15.0),
            ("20秒 1 条", 20.0), ("30秒 1 条", 30.0),
        ]
        for label, sec in _speeds:
            self.cb_interval.addItem(label, sec)
        self.cb_interval.setCurrentIndex(8)  # 默认 20秒/条
        xf.addRow("爬取速度:", self.cb_interval)
        xl.addLayout(xf)
        hb_x = QHBoxLayout()
        self.btn_conv_country = QPushButton("🌐 转中文国名（本地零成本）")
        self.btn_conv_country.clicked.connect(self._convert_country)
        hb_x.addWidget(self.btn_conv_country)
        self.btn_backfill_country = QPushButton("🩹 从 raw_json 反补国名")
        self.btn_backfill_country.setToolTip("Kaggle 旧数据用：解析 raw_json 把 country_name 从原始 JSON 补回来")
        self.btn_backfill_country.clicked.connect(self._backfill_country)
        hb_x.addWidget(self.btn_backfill_country)
        self.btn_strengthen = QPushButton("🕷 开始强化（补中文名）")
        self.btn_strengthen.clicked.connect(self._start_strengthen)
        hb_x.addWidget(self.btn_strengthen)
        self.btn_stop_str = QPushButton("⏹ 停止")
        self.btn_stop_str.clicked.connect(self._stop_strengthen)
        self.btn_stop_str.setEnabled(False)
        hb_x.addWidget(self.btn_stop_str)
        self.btn_reset_str = QPushButton("↺ 重置续跑")
        self.btn_reset_str.setToolTip("清空续跑点，下次强化从头开始")
        self.btn_reset_str.clicked.connect(self._reset_strengthen_resume)
        hb_x.addWidget(self.btn_reset_str)
        xl.addLayout(hb_x)
        self.pb_str = QProgressBar()
        xl.addWidget(self.pb_str)
        self.lbl_task = QLabel("任务进度: 0 / 0（未开始）")
        self.lbl_task.setFont(self._mono_font)
        xl.addWidget(self.lbl_task)
        self.lbl_elapsed = QLabel("已运行时间: 0s（未开始）")
        self.lbl_elapsed.setFont(self._mono_font)
        xl.addWidget(self.lbl_elapsed)
        xl.addWidget(QLabel("说明：强化只补 title_zh / country_name，不依赖主界面扫描，"
                            "可挂机后台运行。"))
        xl.addStretch()
        tabs.addTab(tab_str, "🕷 自动强化")
        # 强化计时器（每秒刷新已运行时间）
        from PyQt5.QtCore import QTimer
        self._str_start_ts = 0
        self._str_total = 0
        self._str_timer = QTimer()
        self._str_timer.setInterval(1000)
        self._str_timer.timeout.connect(self._tick_elapsed)
        # 任务进度刷新定时器（每 10 秒）
        self._task_timer = QTimer()
        self._task_timer.setInterval(10000)
        self._task_timer.timeout.connect(self._tick_task)

        # ===== 标签页6: 数据库 / 索引 =====
        tab_db = QWidget()
        dl = QVBoxLayout(tab_db)

        # 索引状态（搜索性能关键）
        gb_idx = QGroupBox("索引状态（搜索性能关键）")
        gl = QVBoxLayout(gb_idx)
        self.lbl_idx_status = QLabel("点击「刷新索引状态」查看")
        self.lbl_idx_status.setFont(self._mono_font)
        self.lbl_idx_status.setWordWrap(True)
        gl.addWidget(self.lbl_idx_status)
        hb_idx = QHBoxLayout()
        self.btn_refresh_idx = QPushButton("🔄 刷新索引状态")
        self.btn_refresh_idx.clicked.connect(self._refresh_index_status)
        self.btn_build_idx = QPushButton("🔧 建立 / 重建索引")
        self.btn_build_idx.clicked.connect(self._start_build_index)
        hb_idx.addWidget(self.btn_refresh_idx)
        hb_idx.addWidget(self.btn_build_idx)
        gl.addLayout(hb_idx)
        self.pb_idx = QProgressBar()
        gl.addWidget(self.pb_idx)
        self.lbl_idx_phase = QLabel("状态: 空闲")
        self.lbl_idx_phase.setFont(self._mono_font)
        gl.addWidget(self.lbl_idx_phase)
        self.lbl_idx_elapsed = QLabel("已用时间: 0s")
        self.lbl_idx_elapsed.setFont(self._mono_font)
        gl.addWidget(self.lbl_idx_elapsed)
        dl.addWidget(gb_idx)

        # 数据库信息（DB 浏览器基础）
        gb_info = QGroupBox("数据库信息（DB 浏览器）")
        il = QVBoxLayout(gb_info)
        self.lbl_db_info = QLabel("")
        self.lbl_db_info.setFont(self._mono_font)
        self.lbl_db_info.setWordWrap(True)
        il.addWidget(self.lbl_db_info)
        dl.addWidget(gb_info)

        # 数据浏览（DB 浏览器核心）
        gb_browse = QGroupBox("数据浏览（movies 表，分页）")
        bl = QVBoxLayout(gb_browse)
        # 筛选表单
        ff = QFormLayout()
        self.le_browse_kw = QLineEdit()
        self.le_browse_kw.setPlaceholderText("标题包含（中/英文均可）")
        ff.addRow("关键词:", self.le_browse_kw)
        self.cb_browse_year = QComboBox()
        self.cb_browse_year.addItem("全部年份", 0)
        for y in [2030, 2025, 2020, 2015] + list(range(2010, 1909, -10)):
            self.cb_browse_year.addItem(f"{y}年以前", y)
        ff.addRow("年份:", self.cb_browse_year)
        self.cb_browse_zh = QComboBox()
        self.cb_browse_zh.addItems(["全部", "仅含中文名", "仅缺中文名"])
        ff.addRow("中文名:", self.cb_browse_zh)
        self.cb_browse_src = QComboBox()
        self.cb_browse_src.addItem("全部来源", "")
        self.cb_browse_src.addItem("tmdb", "tmdb")
        self.cb_browse_src.addItem("kaggle", "kaggle")
        self.cb_browse_src.addItem("manual", "manual")
        ff.addRow("来源:", self.cb_browse_src)
        bl.addLayout(ff)
        # 按钮行
        hb_b = QHBoxLayout()
        self.btn_browse_query = QPushButton("🔍 查询")
        self.btn_browse_query.clicked.connect(self._browse_query)
        self.btn_browse_prev = QPushButton("◀ 上一页")
        self.btn_browse_prev.clicked.connect(lambda: self._browse_page(-1))
        self.btn_browse_next = QPushButton("下一页 ▶")
        self.btn_browse_next.clicked.connect(lambda: self._browse_page(1))
        self.btn_browse_export = QPushButton("📤 导出 CSV")
        self.btn_browse_export.clicked.connect(self._browse_export)
        hb_b.addWidget(self.btn_browse_query)
        hb_b.addWidget(self.btn_browse_prev)
        hb_b.addWidget(self.btn_browse_next)
        hb_b.addWidget(self.btn_browse_export)
        bl.addLayout(hb_b)
        self.lbl_browse_page = QLabel("第 0 / 0 页（共 0 行）")
        self.lbl_browse_page.setFont(self._mono_font)
        bl.addWidget(self.lbl_browse_page)
        self.tbl_browse = QTableWidget()
        self.tbl_browse.setColumnCount(8)
        self.tbl_browse.setHorizontalHeaderLabels(
            ["ID", "英文标题", "中文标题", "年份", "国家（修订）", "语言", "来源", "缓存时间"])
        self.tbl_browse.horizontalHeader().setStretchLastSection(True)
        self.tbl_browse.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_browse.setMinimumHeight(280)
        bl.addWidget(self.tbl_browse, 1)
        # 导出进度
        self.pb_export = QProgressBar()
        bl.addWidget(self.pb_export)
        dl.addWidget(gb_browse, 1)

        tabs.addTab(tab_db, "🗄 数据库")

        # 索引构建计时器（每秒刷新已用时间）
        self._idx_start_ts = 0
        self._idx_timer = QTimer()
        self._idx_timer.setInterval(1000)
        self._idx_timer.timeout.connect(self._tick_index_elapsed)

        # ===== 日志 =====
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(self._mono_font)
        self.log.setMinimumHeight(200)
        tabs.addTab(self.log, "日志")

        self._refresh_stats()

    def _log(self, msg):
        self.log.append(msg)
        # 独立日志文件（v23.55）：界面与文件双写，便于崩溃后排错
        try:
            _lp = os.path.join(_APP_ROOT, "logs", "tmdb_manager.log")
            os.makedirs(os.path.dirname(_lp), exist_ok=True)
            with open(_lp, "a", encoding="utf-8") as _f:
                import datetime as _dt
                _f.write(f"[{_dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
        except Exception:
            pass

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
        self.worker.progress.connect(
            lambda c, t: self.progress_bar.setValue(int(c * 100 / t)) if t else None)
        self.worker.finished.connect(lambda n: QMessageBox.information(
            self, "导入完成", f"成功导入 {n:,} 条记录"))
        self.worker.finished.connect(self._refresh_stats)
        self.worker.start()

    # ===== 自动强化 =====
    def _save_apikey(self):
        k = self.le_apikey.text().strip()
        if not k:
            QMessageBox.warning(self, "提示", "请输入 API Key")
            return
        save_config_key("tmdb_api_key", k)
        QMessageBox.information(self, "已保存", "TMDB API Key 已写入 config.json")

    def _convert_country(self):
        if getattr(self, "conv_worker", None) and self.conv_worker.isRunning():
            QMessageBox.information(self, "提示", "转中文国名进行中，请稍候")
            return
        self.conv_worker = ConvertCountryWorker()
        self.conv_worker.log.connect(self._log)
        self.conv_worker.done.connect(lambda n: (self._refresh_stats(),
                                                 self._log("📊 统计已刷新")))
        self.conv_worker.start()

    def _backfill_country(self):
        if getattr(self, "backfill_worker", None) and self.backfill_worker.isRunning():
            QMessageBox.information(self, "提示", "反补进行中，请稍候")
            return
        self.backfill_worker = BackfillCountryWorker()
        self.backfill_worker.log.connect(self._log)
        self.backfill_worker.done.connect(lambda n: (self._refresh_stats(),
                                                     self._log("📊 统计已刷新")))
        self.backfill_worker.start()

    def _start_strengthen(self):
        k = self.le_apikey.text().strip() or load_config().get("tmdb_api_key", "")
        if not k:
            QMessageBox.warning(self, "提示", "请先填写并保存 TMDB API Key")
            return
        # 关键修复：断开旧 worker 的所有信号，避免前一次强化的剩余日志
        # /进度混入新运行的显示（之前会出现"强化完成"后日志还在滚的现象）
        old = getattr(self, "str_worker", None)
        if old is not None:
            try:
                old.log.disconnect()
                old.progress.disconnect()
                old.done.disconnect()
            except (TypeError, RuntimeError):
                pass
        interval = float(self.cb_interval.currentData() or 20)
        # 断点续读：读 state 文件，拿到上次的 last_id 作为本次起点
        from core.tmdb_cache import TmdbCache, CACHE_DIR
        import json as _json
        state_path = os.path.join(CACHE_DIR, "strengthen_resume.json")
        start_after_id = 0
        try:
            if os.path.exists(state_path):
                with open(state_path, "r", encoding="utf-8") as _f:
                    start_after_id = int(_json.load(_f).get("last_id", 0) or 0)
        except Exception:
            start_after_id = 0
        if start_after_id:
            self._log(f"📌 续跑模式：从 id>{start_after_id:,} 开始（清空续跑点：点「重置续跑」按钮）")
        # 待补总数也按续跑点算，避免分母虚高
        try:
            self._str_total = TmdbCache()._get_conn().execute(
                "SELECT COUNT(*) FROM movies "
                "WHERE title_zh = '' AND title_en != '' AND id > ?",
                (start_after_id,),
            ).fetchone()[0]
        except Exception:
            self._str_total = 0
        self.lbl_task.setText(f"任务进度: 0 / {self._str_total:,}")
        self.pb_str.setValue(0)
        self.str_worker = StrengthenWorker(k, interval, start_after_id=start_after_id)
        self.str_worker.log.connect(self._log)
        self.str_worker.progress.connect(
            lambda p, t, u: (setattr(self, "_str_done", p),
                             self.pb_str.setValue(int(p * 100 / self._str_total)) if self._str_total else None))
        self.str_worker.done.connect(lambda p, u: (
            self._log(f"✅ 强化完成：处理 {p:,} 条，更新 {u:,} 条，用时 {self._fmt_elapsed()}"),
            self.pb_str.setValue(100),
            self.lbl_task.setText(f"任务进度: {p:,} / {self._str_total:,}（已完成）"),
            self._refresh_stats(), self._str_timer.stop(), self._task_timer.stop(),
            self.btn_strengthen.setEnabled(True), self.btn_stop_str.setEnabled(False)))
        self.str_worker.start()
        self.btn_strengthen.setEnabled(False)
        self.btn_stop_str.setEnabled(True)
        # 启动计时 + 任务进度刷新
        import time as _t
        self._str_start_ts = _t.time()
        self._str_done = 0
        self.lbl_elapsed.setText("已运行时间: 0s")
        self._str_timer.start()
        self._task_timer.start()

    def _tick_task(self):
        done = getattr(self, "_str_done", 0)
        self.lbl_task.setText(f"任务进度: {done:,} / {self._str_total:,}")

    def _tick_elapsed(self):
        import time as _t
        if self._str_start_ts:
            self.lbl_elapsed.setText(f"已运行时间: {self._fmt_elapsed()}")

    def _fmt_elapsed(self, start_ts=None):
        import time as _t
        base = start_ts if start_ts is not None else self._str_start_ts
        sec = int(_t.time() - (base or _t.time()))
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}h{m}m{s}s"
        if m:
            return f"{m}m{s}s"
        return f"{s}s"

    def _stop_strengthen(self):
        if self.str_worker:
            self.str_worker.stop()
            self._log("⏹ 已发送停止信号，等待当前请求完成后中止...")
            self._log(f"已运行时间: {self._fmt_elapsed()}")
            done = getattr(self, "_str_done", 0)
            self._log(f"任务进度: {done:,} / {self._str_total:,}")
        self._str_timer.stop()
        self._task_timer.stop()
        self.btn_strengthen.setEnabled(True)
        self.btn_stop_str.setEnabled(False)

    def _reset_strengthen_resume(self):
        """清空续跑点，下次强化从首条未完成记录开始。"""
        from core.tmdb_cache import CACHE_DIR
        state_path = os.path.join(CACHE_DIR, "strengthen_resume.json")
        try:
            if os.path.exists(state_path):
                os.remove(state_path)
                self._log("↺ 已清空续跑点，下次「开始强化」将从头开始")
            else:
                self._log("↺ 续跑点本就不存在，无需重置")
        except Exception as e:
            self._log(f"重置续跑点失败: {e}")


    # ===== 数据库 / 索引 =====
    def _refresh_index_status(self):
        try:
            from core.tmdb_cache import TmdbCache
            cache = TmdbCache()
            st = cache.index_status()
            lines = []
            lines.append(f"数据库路径: {cache.db_path}")
            lines.append(f"数据行数:   {st['row_count']:,}")
            mb = st['db_size'] // 1024 // 1024 if st['db_size'] else 0
            lines.append(f"数据库大小: {mb} MB")
            lines.append("─" * 32)
            def _mark(b):
                return "✓ 已建" if b else "✗ 缺失"
            lines.append(f"搜索索引  idx_movies_search : {_mark(st['has_search_index'])}")
            lines.append(f"TMDB索引  idx_movies_tmdb_id: {_mark(st['has_tmdb_id_index'])}")
            lines.append(f"年份索引  idx_movies_year    : {_mark(st['has_year_index'])}")
            for nm in ("idx_movies_search", "idx_movies_tmdb_id", "idx_movies_year"):
                sz = st['index_sizes'].get(nm)
                if sz is not None:
                    lines.append(f"    └ {nm} 占用: {sz // 1024} KB")
                else:
                    lines.append(f"    └ {nm} 占用: —（本环境不可用）")
            self.lbl_idx_status.setText("\n".join(lines))
            # 数据库信息（列结构）
            cols = cache._get_conn().execute("PRAGMA table_info(movies)").fetchall()
            col_names = ", ".join(c[1] for c in cols)
            self.lbl_db_info.setText(
                f"表 movies 列数: {len(cols)}\n"
                f"全部索引: {', '.join(st['indexes']) if st['indexes'] else '（无）'}\n"
                f"列: {col_names}"
            )
            if not st['has_search_index']:
                self._log("⚠ 检测到搜索索引缺失：泛搜索会退化为全表扫描（大库极慢），"
                          "请点「建立/重建索引」")
        except Exception as e:
            self.lbl_idx_status.setText(f"⚠ 读取索引状态失败: {e}")

    def _start_build_index(self):
        if getattr(self, "idx_worker", None) and self.idx_worker.isRunning():
            QMessageBox.information(self, "提示", "索引构建进行中，请稍候")
            return
        self.idx_worker = IndexBuildWorker()
        self.idx_worker.progress.connect(self._on_index_progress)
        self.idx_worker.log.connect(self._log)
        self.idx_worker.done.connect(self._on_index_done)
        self.idx_worker.start()
        self.btn_build_idx.setEnabled(False)
        self.btn_refresh_idx.setEnabled(False)
        self.pb_idx.setValue(0)
        self.lbl_idx_phase.setText("状态: 构建中…")
        self._idx_start_ts = time.time()
        self.lbl_idx_elapsed.setText("已用时间: 0s")
        self._idx_timer.start()

    def _on_index_progress(self, step, total, phase):
        self.pb_idx.setValue(int(step * 100 / total) if total else 0)
        self.lbl_idx_phase.setText(f"状态: {phase}（{step}/{total}）")

    def _on_index_done(self, msg):
        self._idx_timer.stop()
        self.pb_idx.setValue(100)
        self.lbl_idx_phase.setText("状态: 完成 ✓")
        self.lbl_idx_elapsed.setText(f"已用时间: {self._fmt_elapsed(self._idx_start_ts)}")
        self._log(msg)
        self.btn_build_idx.setEnabled(True)
        self.btn_refresh_idx.setEnabled(True)
        self._refresh_index_status()

    def _tick_index_elapsed(self):
        if self._idx_start_ts:
            self.lbl_idx_elapsed.setText(
                f"已用时间: {self._fmt_elapsed(self._idx_start_ts)}")


    # ===== 数据浏览 =====
    def _current_browse_filters(self):
        ym = int(self.cb_browse_year.currentData() or 0)
        zh_map = {"全部": "all", "仅含中文名": "has", "仅缺中文名": "missing"}
        return {
            "keyword": self.le_browse_kw.text().strip() or None,
            "year_from": None,
            "year_to": ym if ym else None,
            "zh": zh_map.get(self.cb_browse_zh.currentText(), "all"),
            "source": self.cb_browse_src.currentData() or None,
        }

    def _browse_query(self):
        kw = self.le_browse_kw.text().strip()
        self._log(f"🔍 数据浏览查询: 关键词='{kw}'")
        self._browse_filters = self._current_browse_filters()
        self._browse_page_no = 1
        self._run_browse()

    def _browse_page(self, delta):
        if not getattr(self, "_browse_filters", None):
            self._browse_query()
            return
        new = getattr(self, "_browse_page_no", 1) + delta
        if new < 1:
            return
        self._browse_page_no = new
        self._run_browse()

    def _run_browse(self):
        if getattr(self, "browse_worker", None) and self.browse_worker.isRunning():
            self._log("⚠ 查询进行中，请等待完成")
            self.btn_browse_query.setEnabled(True)
            return
        self.btn_browse_query.setEnabled(False)
        self.browse_worker = DbBrowseWorker(self._browse_filters, self._browse_page_no, 200)
        self.browse_worker.result.connect(self._on_browse_result)
        self.browse_worker.log.connect(self._log)
        self.browse_worker.start()

    def _on_browse_result(self, rows, total, page, page_size):
        try:
            self.btn_browse_query.setEnabled(True)
            pages = (total + page_size - 1) // page_size or 1
            self.lbl_browse_page.setText(f"第 {page} / {pages} 页（共 {total:,} 行）")
            self.tbl_browse.setRowCount(len(rows))
            for i, r in enumerate(rows):
                self.tbl_browse.setItem(i, 0, QTableWidgetItem(str(r.get("id") or "")))
                self.tbl_browse.setItem(i, 1, QTableWidgetItem(r.get("title_en") or ""))
                self.tbl_browse.setItem(i, 2, QTableWidgetItem(r.get("title_zh") or ""))
                self.tbl_browse.setItem(i, 3, QTableWidgetItem(str(r.get("year") or "")))
                self.tbl_browse.setItem(i, 4, QTableWidgetItem(r.get("country_revised") or ""))
                self.tbl_browse.setItem(i, 5, QTableWidgetItem(r.get("language") or ""))
                self.tbl_browse.setItem(i, 6, QTableWidgetItem(r.get("source") or ""))
                self.tbl_browse.setItem(i, 7, QTableWidgetItem(r.get("cached_at") or ""))
        except Exception as e:
            import traceback
            self.btn_browse_query.setEnabled(True)
            self._log(f"⚠ 浏览结果显示失败: {e}")
            self._log(traceback.format_exc())

    def _browse_export(self):
        if getattr(self, "export_worker", None) and self.export_worker.isRunning():
            QMessageBox.information(self, "提示", "导出进行中，请稍候")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", "tmdb_export.csv", "CSV (*.csv)")
        if not path:
            return
        filters = getattr(self, "_browse_filters", None) or self._current_browse_filters()
        self.export_worker = DbExportWorker(filters, path)
        self.export_worker.progress.connect(
            lambda w, t: self.pb_export.setValue(int(w * 100 / t)) if t else None)
        self.export_worker.log.connect(self._log)
        self.export_worker.done.connect(
            lambda n, p: (self.pb_export.setValue(100),
                          self._log(f"✅ 已导出 {n:,} 行到 {p}")))
        self.pb_export.setValue(0)
        self.export_worker.start()


def _exc_hook(exc_type, exc_val, exc_tb):
    # v23.55: 全局未捕获异常写入独立日志，避免"默默退出"无迹可寻
    import traceback
    try:
        _lp = os.path.join(_APP_ROOT, "logs", "tmdb_manager.log")
        os.makedirs(os.path.dirname(_lp), exist_ok=True)
        with open(_lp, "a", encoding="utf-8") as _f:
            import datetime as _dt
            _f.write(f"[{_dt.datetime.now():%Y-%m-%d %H:%M:%S}] [UNCAUGHT] "
                     f"{''.join(traceback.format_exception(exc_type, exc_val, exc_tb))}\n")
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_val, exc_tb)


def main():
    sys.excepthook = _exc_hook
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
