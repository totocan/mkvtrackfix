# -*- coding: utf-8 -*-
"""设置对话框（v9）：AI 模型、工具路径、OCR 配置、保留策略、产地判断、字体设置。

改进(v9)：
  - 新增 OCR 配置组：Tesseract 路径、跳过秒数、取样秒数、帧间隔、语言包、最小文本长度
  - 新增 GUI 字体设置：字体族、界面字号、日志字号
  - 工具路径：仅保留 mkvmerge 与 Tesseract（Subtitle Edit 已彻底移除）
  - 采样时间段改为"起始秒+时长"（默认90~180秒）
  - 音轨策略简化：精简+保留最佳+国产去掉英语
  - 字幕策略：移除繁体+有双语去纯英文
  - 新增豆瓣查询开关
"""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QComboBox, QSpinBox, QLineEdit,
    QCheckBox, QPushButton, QGroupBox, QHBoxLayout, QDialogButtonBox,
    QFileDialog, QLabel, QFontComboBox, QScrollArea, QWidget,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
import os
from core import config as config_mod


class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("设置")
        # v15：默认 1500×800，4K/高分屏够用 + 可拖拽缩放
        self.resize(1500, 800)
        self._build()
        self._load()

    # ------------------------------------------------------------------
    def _row(self, layout, label, widget):
        layout.addRow(label, widget)
        return widget

    def _build(self):
        # 外层：滚动区域（内容超出可视区时出现滚动条）
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll = scroll

        # 内部内容容器
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(12, 12, 12, 12)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        # —— AI 模型 ——
        g1 = QGroupBox("AI 语音识别 (faster-whisper)")
        f1 = QFormLayout(g1)
        self.c_model = QComboBox()
        self.c_model.addItems(["tiny", "base", "small", "medium", "large-v3"])
        self._row(f1, "模型大小", self.c_model)
        self.c_device = QComboBox()
        self.c_device.addItems(["cpu", "cuda", "auto"])
        self._row(f1, "设备", self.c_device)
        self.c_compute = QComboBox()
        self.c_compute.addItems(["int8", "int16", "float16", "float32"])
        self._row(f1, "计算精度", self.c_compute)
        self.sp_threads = QSpinBox()
        self.sp_threads.setRange(0, 32)
        self._row(f1, "CPU 线程(0=自动)", self.sp_threads)
        # v21.2: 用文本框替代原来的"取样起始秒"，直接支持多个采样起点
        self.le_sample_segments = QLineEdit()
        self.le_sample_segments.setPlaceholderText("600,1000,1500")
        self._row(f1, "音轨采样起点(逗号分隔,秒)", self.le_sample_segments)
        self.c_sample_duration = QComboBox()
        self.c_sample_duration.addItems(["5", "10", "15", "20"])
        self._row(f1, "每段采样时长(秒)", self.c_sample_duration)
        self.c_zh = QComboBox()
        self.c_zh.addItems(["cmn", "yue"])
        self._row(f1, "中文(zh)默认映射", self.c_zh)
        self.c_redetect = QComboBox()
        self.c_redetect.addItems(["all", "und_only", "skip"])
        self._row(f1, "音轨重识别", self.c_redetect)
        root.addWidget(g1)

        # —— 电影产地判断 ——
        g6 = QGroupBox("电影产地判断")
        v6 = QVBoxLayout(g6)
        self.bx_douban = QCheckBox("开启TMDB联网查询（themoviedb.org，判断电影产地/原生语言）")
        self.bx_domestic_drop_eng = QCheckBox("国产电影去掉英语音轨")
        v6.addWidget(self.bx_douban)
        v6.addWidget(self.bx_domestic_drop_eng)
        lbl_douban = QLabel("关闭后无法区分国产/外国电影，降级到保留英语+普通话策略")
        lbl_douban.setWordWrap(True)
        v6.addWidget(lbl_douban)
        root.addWidget(g6)

        # —— 外部工具 ——
        g2 = QGroupBox("外部工具（留空自动探测）")
        f2 = QFormLayout(g2)
        self.le_mkv = QLineEdit()
        self._browse_row(f2, "mkvmerge", self.le_mkv, exe=True)
        root.addWidget(g2)

        # —— RapidOCR 配置（v22 替代 PaddleOCR/Tesseract）——
        g_ocr = QGroupBox("RapidOCR 配置（图像字幕 sup/PGS 识别）")
        f_ocr = QFormLayout(g_ocr)
        self.sp_ocr_skip = QSpinBox()
        self.sp_ocr_skip.setRange(0, 3600)
        self.sp_ocr_skip.setSuffix(" 秒")
        self._row(f_ocr, "抽帧跳过前 N 秒", self.sp_ocr_skip)
        self.sp_ocr_attempts = QSpinBox()
        self.sp_ocr_attempts.setRange(1, 20)
        self.sp_ocr_attempts.setSuffix(" 次")
        self._row(f_ocr, "最多尝试次数", self.sp_ocr_attempts)
        self.sp_ocr_min_len = QSpinBox()
        self.sp_ocr_min_len.setRange(0, 5000)
        self._row(f_ocr, "最小文本长度(低于则回退推断)", self.sp_ocr_min_len)
        root.addWidget(g_ocr)

        # —— mkvextract 字幕抽取超时 ——
        g_ext = QGroupBox("mkvextract 字幕抽取")
        f_ext = QFormLayout(g_ext)
        self.sp_sub_extract_timeout = QSpinBox()
        self.sp_sub_extract_timeout.setRange(10, 600)
        self.sp_sub_extract_timeout.setSuffix(" 秒")
        self.sp_sub_extract_timeout.setToolTip(
            "13GB+ 大文件可能需要加大此值。\n"
            "如果日志中出现「mkvextract 抽取字幕流超时」错误，请将此值增大。")
        self._row(f_ext, "超时时间", self.sp_sub_extract_timeout)
        root.addWidget(g_ext)

        # —— 音轨策略 ——
        g3 = QGroupBox("音轨保留策略")
        v3 = QVBoxLayout(g3)
        self.bx_reduce = QCheckBox("精简多音轨（否则全部保留）")
        self.bx_best = QCheckBox("同语言多音轨仅保留音质最佳一条")
        v3.addWidget(self.bx_reduce)
        v3.addWidget(self.bx_best)
        lbl_audio = QLabel("国产电影：只保留普通话/粤语\n外国电影：保留英语+普通话\n兜底：至少保留一条音轨")
        lbl_audio.setWordWrap(True)
        v3.addWidget(lbl_audio)
        root.addWidget(g3)

        # —— 字幕策略 ——
        g4 = QGroupBox("字幕策略")
        v4 = QVBoxLayout(g4)
        self.bx_rmtrad = QCheckBox("移除繁体中文字幕")
        self.bx_rm_redundant_simplified = QCheckBox(
            "有简中英双语时移除独立简体中文（冗余）")
        self.bx_rm_bilingual_eng = QCheckBox("有简中/双语时去掉纯英文字幕")
        v4.addWidget(self.bx_rmtrad)
        v4.addWidget(self.bx_rm_redundant_simplified)
        v4.addWidget(self.bx_rm_bilingual_eng)
        lbl_sub = QLabel(
            "保留优先级：简中英双语 > 简体中文(无双语时) > 纯英文(仅无中文时)；"
            "繁体/其他移除")
        lbl_sub.setWordWrap(True)
        v4.addWidget(lbl_sub)
        root.addWidget(g4)

        # —— GUI 字体设置 —— 【v9 新增】
        g_font = QGroupBox("界面字体（解决文字偏小问题）")
        f_font = QFormLayout(g_font)
        self.cb_font_family = QFontComboBox()
        self._row(f_font, "字体族（空=系统默认）", self.cb_font_family)
        self.sp_gui_size = QSpinBox()
        self.sp_gui_size.setRange(8, 24)
        self.sp_gui_size.setSuffix(" pt")
        self._row(f_font, "界面字号（菜单/表格/按钮）", self.sp_gui_size)
        self.sp_log_size = QSpinBox()
        self.sp_log_size.setRange(7, 20)
        self.sp_log_size.setSuffix(" pt")
        self._row(f_font, "日志区域字号（等宽字体）", self.sp_log_size)
        btn_preview = QPushButton("预览字体效果")
        btn_preview.clicked.connect(self._preview_font)
        f_font.addRow(btn_preview)
        root.addWidget(g_font)

        # —— 输出 ——
        g5 = QGroupBox("输出")
        f5 = QFormLayout(g5)
        self.bx_smart_rename = QCheckBox("智能重命名（推荐）")
        self.bx_smart_rename.setChecked(True)
        self.bx_smart_rename.toggled.connect(self._on_smart_rename_toggle)
        f5.addRow(self.bx_smart_rename)
        # 命名规则说明
        rule_label = QLabel(
            "命名规则:\n"
            "  [中文名.]英文名.年份.音频编码.声道.fixed.mkv\n"
            "  · 优先 TMDB 查英文名（刮削软件最佳识别）\n"
            "  · 文件夹无中文时自动加入中文前缀\n"
            "  · 视频编码未知自动省略\n"
            "  · 重复跑自动叠加 .fixed\n"
            "  · 关闭后使用下方固定后缀")
        rule_label.setStyleSheet("color:#888; font-size:9pt; padding-left:20px;")
        f5.addRow(rule_label)
        self.le_suffix = QLineEdit()
        self._row(f5, "固定后缀（智能重命名关闭时）", self.le_suffix)
        self.bx_verbose = QCheckBox(
            "详细记录第三方工具输出(ffmpeg/mkvmerge/RapidOCR)")
        f5.addRow(self.bx_verbose)
        self.bx_debug = QCheckBox(
            "调试模式（保留临时文件供排查：WAV/OCR截图等，不自动清理）")
        f5.addRow(self.bx_debug)
        self.bx_keep_ocr = QCheckBox(
            "保留OCR帧（滑动窗口只删视频缓存，保留OCR截图/音轨WAV供排查）")
        f5.addRow(self.bx_keep_ocr)
        root.addWidget(g5)

        # —— 按钮 ——
        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _browse_row(self, form, label, line_edit, exe=False):
        h = QHBoxLayout()
        h.addWidget(line_edit)
        b = QPushButton("浏览")
        b.clicked.connect(lambda: self._browse(line_edit, exe))
        h.addWidget(b)
        form.addRow(label, h)

    def _browse(self, line_edit, exe):
        if exe:
            p, _ = QFileDialog.getOpenFileName(
                self, "选择可执行文件", "",
                "Executable (*.exe);;All (*.*)")
        else:
            p = QFileDialog.getExistingDirectory(self, "选择目录")
        if p:
            line_edit.setText(p)

    def _relativize(self, raw):
        """把用户在浏览框选中的绝对路径，若位于程序目录内则转为相对路径。

        相对路径以 tools\\ 等前缀存储，迁移后自动拼接新程序根，不再失效。
        若路径在程序目录外（用户自定义安装），保留绝对路径。
        """
        raw = (raw or "").strip()
        if not raw:
            return ""
        from core import config as cfg_mod
        root = cfg_mod.app_root()
        try:
            abs_raw = os.path.normcase(os.path.normpath(os.path.abspath(raw)))
            root_norm = os.path.normcase(os.path.normpath(root))
            if abs_raw.startswith(root_norm + os.sep) or abs_raw == root_norm:
                rel = os.path.relpath(abs_raw, root_norm)
                return rel.replace("/", "\\")
        except Exception:
            pass
        return raw

    def _on_smart_rename_toggle(self, enabled):
        self.le_suffix.setEnabled(not enabled)

    def _preview_font(self):
        """预览当前选择的字体效果。"""
        family = self.cb_font_family.currentText() or ""
        gui_sz = self.sp_gui_size.value()
        log_sz = self.sp_log_size.value()
        msg = (
            f"<b>界面字体预览：</b><br>"
            f"字体族：<code>{family or '(系统默认)'}</code> | "
            f"字号：{gui_sz}pt / 日志{log_sz}pt<br>"
            f"<span style='font-size:{gui_sz}pt'>这是界面文字大小示例</span> | "
            f"<span style='font-size:{log_sz}pt;font-family:Consolas,"
            f"monospace;background:#1e1e1e;color:#d0d0d0;padding:2px 6px;'>"
            f"这是日志文字大小示例</span>"
        )
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.information(self, "字体预览", msg)

    # ------------------------------------------------------------------
    def _load(self):
        c = self.cfg
        # v21.2: 所有 fallback 值改为从 config_mod.DEFAULTS 读取，消除硬编码不一致
        _D = config_mod.DEFAULTS
        self.c_model.setCurrentText(c.get("model_size", _D.get("model_size", "medium")))
        self.c_device.setCurrentText(c.get("device", _D.get("device", "cpu")))
        self.c_compute.setCurrentText(c.get("compute_type", _D.get("compute_type", "int8")))
        self.sp_threads.setValue(int(c.get("cpu_threads", _D.get("cpu_threads", 0))))
        self.le_sample_segments.setText(c.get("sample_segments", _D.get("sample_segments", "600,1000,1500")))
        idx = self.c_sample_duration.findText(str(int(c.get("sample_duration_seconds", 10))))
        if idx >= 0:
            self.c_sample_duration.setCurrentIndex(idx)
        self.c_zh.setCurrentText(c.get("zh_audio_as", _D.get("zh_audio_as", "cmn")))
        self.c_redetect.setCurrentText(c.get("audio_redetect", _D.get("audio_redetect", "all")))
        self.le_mkv.setText(c.get("mkvmerge_path", "") or "")

        # RapidOCR 配置（v22）
        self.sp_ocr_skip.setValue(int(c.get("ocr_skip_seconds", _D.get("ocr_skip_seconds", 300))))
        self.sp_ocr_attempts.setValue(int(c.get("ocr_max_attempts", _D.get("ocr_max_attempts", 4))))
        self.sp_ocr_min_len.setValue(int(c.get("ocr_min_text_len", _D.get("ocr_min_text_len", 30))))

        # mkvextract 字幕抽取超时
        self.sp_sub_extract_timeout.setValue(
            int(c.get("subtitle_extract_timeout", _D.get("subtitle_extract_timeout", 180))))

        # 字体设置
        font_family = c.get("gui_font_family", "") or ""
        if font_family:
            idx = self.cb_font_family.findText(font_family)
            if idx >= 0:
                self.cb_font_family.setCurrentIndex(idx)
        else:
            self.cb_font_family.setCurrentIndex(0)
        self.sp_gui_size.setValue(int(c.get("gui_font_size", 10)))
        self.sp_log_size.setValue(int(c.get("log_font_size", 9)))

        # 其他
        self.bx_douban.setChecked(c.get("douban_enabled", True))
        self.bx_domestic_drop_eng.setChecked(c.get("domestic_drop_english", True))
        self.bx_reduce.setChecked(c.get("audio_reduce", True))
        self.bx_best.setChecked(c.get("audio_keep_best_only", True))
        self.bx_rmtrad.setChecked(c.get("sub_remove_traditional", True))
        self.bx_rm_redundant_simplified.setChecked(
            c.get("sub_remove_redundant_simplified_if_bilingual", True))
        self.bx_rm_bilingual_eng.setChecked(
            c.get("sub_remove_pure_english_if_bilingual", True))
        self.bx_smart_rename.setChecked(c.get("smart_rename", True))
        self.le_suffix.setText(c.get("output_suffix", ".fixed"))
        self.le_suffix.setEnabled(not c.get("smart_rename", True))
        self.bx_verbose.setChecked(c.get("verbose_tools", False))
        self.bx_debug.setChecked(c.get("debug_mode", False))
        self.bx_keep_ocr.setChecked(c.get("keep_ocr_frames", False))

    def accept(self):
        try:
            c = self.cfg
            # AI 模型
            c["model_size"] = self.c_model.currentText()
            c["device"] = self.c_device.currentText()
            c["compute_type"] = self.c_compute.currentText()
            c["cpu_threads"] = self.sp_threads.value()
            c["sample_segments"] = self.le_sample_segments.text().strip() or "600,1000,1500"
            c["sample_duration_seconds"] = int(self.c_sample_duration.currentText())
            c["zh_audio_as"] = self.c_zh.currentText()
            c["audio_redetect"] = self.c_redetect.currentText()
            # 工具路径（若是程序目录内路径则存相对路径，便于迁移）
            c["mkvmerge_path"] = self._relativize(self.le_mkv.text().strip())
            # 产地判断
            c["douban_enabled"] = self.bx_douban.isChecked()
            c["domestic_drop_english"] = self.bx_domestic_drop_eng.isChecked()
            # RapidOCR 配置
            c["ocr_skip_seconds"] = self.sp_ocr_skip.value()
            c["ocr_max_attempts"] = self.sp_ocr_attempts.value()
            c["ocr_min_text_len"] = self.sp_ocr_min_len.value()
            # mkvextract 字幕抽取超时
            c["subtitle_extract_timeout"] = self.sp_sub_extract_timeout.value()
            # 字体设置
            c["gui_font_family"] = self.cb_font_family.currentText().strip()
            c["gui_font_size"] = self.sp_gui_size.value()
            c["log_font_size"] = self.sp_log_size.value()
            # 音轨策略
            c["audio_reduce"] = self.bx_reduce.isChecked()
            c["audio_keep_best_only"] = self.bx_best.isChecked()
            # 字幕策略
            c["sub_remove_traditional"] = self.bx_rmtrad.isChecked()
            c["sub_remove_redundant_simplified_if_bilingual"] = \
                self.bx_rm_redundant_simplified.isChecked()
            c["sub_remove_pure_english_if_bilingual"] = self.bx_rm_bilingual_eng.isChecked()
            # 输出
            c["smart_rename"] = self.bx_smart_rename.isChecked()
            c["output_suffix"] = self.le_suffix.text().strip() or ".fixed"
            c["verbose_tools"] = self.bx_verbose.isChecked()
            c["debug_mode"] = self.bx_debug.isChecked()
            c["keep_ocr_frames"] = self.bx_keep_ocr.isChecked()
            # 持久化
            config_mod.save(c)
            # 应用字体到当前窗口（即时生效）
            self._apply_font_to_app(c)
        except Exception as e:
            import traceback, sys
            sys.stderr.write(f"Settings save error:\n{traceback.format_exc()}\n")
        super().accept()

    def _apply_font_to_app(self, cfg):
        """将字体设置应用到 QApplication 全局。"""
        try:
            from PyQt5.QtWidgets import QApplication
            app = QApplication.instance()
            if not app:
                return
            font_family = cfg.get("gui_font_family") or ""
            font_size = int(cfg.get("gui_font_size", 10))

            font = app.font()
            if font_family:
                font.setFamily(font_family)
            if font_size >= 8:
                font.setPointSize(font_size)
            app.setFont(font)
        except Exception:
            pass
