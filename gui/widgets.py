# -*- coding: utf-8 -*-
"""GUI 辅助组件：带颜色的日志框（支持可配置字号）。"""
import sys
from PyQt5.QtWidgets import QTextEdit, QDialog, QVBoxLayout, QCheckBox, \
    QLineEdit, QFormLayout, QDialogButtonBox, QLabel
from PyQt5.QtCore import pyqtSlot


class LogWidget(QTextEdit):
    """带级别颜色的日志框（info/warn/error）。

    改进(v9)：支持通过构造参数或 set_log_font_size() 动态调整日志区域字体大小，
              替代原来硬编码的 11px（在高 DPI 屏幕上过小难以阅读）。

    每一行除了显示到界面，还会写入 sys.stderr —— 在 main.py 里 sys.stderr
    已被重定向到 logs/ 文件，因此所有日志都会落盘，崩溃时也能查到现场。
    """

    COLORS = {
        "info": "#d0d0d0",
        "warn": "#ffcc66",
        "error": "#ff6666",
        "ok": "#66cc99",
        "keep": "#64b5f6",        # 淡蓝：保留
        "remove": "#ff9100",      # 橙色：移除
    }

    def __init__(self, parent=None, font_size=9):
        super().__init__(parent)
        self._log_font_size = max(font_size, 7)
        self.setReadOnly(True)
        self.setLineWrapMode(QTextEdit.WidgetWidth)  # v21.2: 自动换行，便于查看长日志
        self._update_stylesheet()

    def _update_stylesheet(self):
        sz = self._log_font_size
        self.setStyleSheet(
            f"QTextEdit{{background:#1e1e1e;color:#d0d0d0;"
            f"font-family:Consolas,Menlo,monospace;font-size:{sz}pt;}}")

    def set_log_font_size(self, size):
        """动态更新日志区域等宽字体大小（pt）。"""
        self._log_font_size = max(size, 7)
        self._update_stylesheet()

    @pyqtSlot(str, str)
    def log(self, msg, level="info"):
        color = self.COLORS.get(level, "#d0d0d0")
        esc = (msg.replace("&", "&amp;").replace("<", "&lt;")
               .replace(">", "&gt;"))
        # 方案A：仅在滚动条位于底部时自动跟随
        sb = self.verticalScrollBar()
        old_val = sb.value()
        old_max = sb.maximum()
        at_bottom = old_val >= old_max - 5

        self.append(f'<span style="color:{color}">{esc}</span>')

        if not at_bottom:
            # 用户往上翻看过历史：恢复滚动条位置
            new_max = sb.maximum()
            sb.setValue(min(old_val, new_max))
        # 同时写文件（落盘，崩溃可查）
        try:
            sys.stderr.write(f"[{level}] {msg}\n")
        except Exception:
            pass


def fmt_plan(tracks):
    """把 tracks 的保留/移除计划汇总成多行文字（每轨道一行）。"""
    lines = []
    for t in tracks:
        if t.track_type == "audio":
            name = t.detected_name or t.detected_iso or t.language_norm or "?"
            if t.action == "keep":
                lines.append(f"保留 audio#{t.track_id} {name}")
            else:
                lines.append(f"移除 audio#{t.track_id} {name}")
        elif t.track_type == "subtitle":
            name = t.detected_name or t.detected_iso or t.language_norm or "?"
            if t.action == "keep":
                lines.append(f"保留 sub#{t.track_id} {name}")
            else:
                lines.append(f"移除 sub#{t.track_id} {name}")
    return "\n".join(lines) if lines else "无变更"


