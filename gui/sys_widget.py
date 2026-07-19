# -*- coding: utf-8 -*-
"""
系统资源监控 UI 组件（v22：CPU / MEM / NET / DISK）。
NET 和 DSK 拆分为上下行/读写双线，不同颜色叠加。
"""
import threading
from collections import deque

from PyQt5.QtWidgets import QWidget, QHBoxLayout, QLabel, QSizePolicy
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPainter, QColor, QPen, QFont, QPainterPath

from core import sys_monitor


# ─────────────────────────────────────────────
# Sparkline — 迷你折线图（60 数据点，支持多色叠加）
# ─────────────────────────────────────────────
class Sparkline(QWidget):
    def __init__(self, parent=None, width=210, height=28, colors=None):
        super().__init__(parent)
        self._w = width
        self._h = height
        # colors: [(队列名, 颜色)]
        self._series = []
        if colors:
            for name, color in colors:
                self._series.append({
                    "name": name,
                    "data": deque(maxlen=60),
                    "color": QColor(color),
                })
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background-color: #0a0a0a; border: none;")
        self.setFixedSize(width, height)

    def push(self, name, val):
        for s in self._series:
            if s["name"] == name:
                s["data"].append(val)
                break
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self._w - 4
        h = self._h - 4
        if h <= 1 or w <= 1:
            return
        for s in self._series:
            data = s["data"]
            if len(data) < 2:
                continue
            mn, mx = min(data), max(data)
            rng = mx - mn if mx != mn else 1
            n = len(data)
            path = QPainterPath()
            for i, v in enumerate(data):
                x = 2 + w * i / max(n - 1, 1)
                y = 2 + h - h * (v - mn) / rng
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            p.setPen(QPen(s["color"], 2))
            p.drawPath(path)
        p.end()


# ─────────────────────────────────────────────
# 资源标签 + Sparkline（单数据 / 多数据）
# ─────────────────────────────────────────────
class _MetricBlock(QWidget):
    def __init__(self, label, spark_args, parent=None):
        super().__init__(parent)
        # v22: 恢复黑底高对比度（v21.2 原始风格）
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background-color: #0a0a0a; border: none;")
        self._label = QLabel(label)
        self._label.setFont(QFont("Consolas", 9, QFont.Bold))
        self._label.setStyleSheet("color:#ffffff;background-color:#0a0a0a;border:none;")
        self._spark = Sparkline(**spark_args)
        hl = QHBoxLayout(self)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)
        hl.addWidget(self._label)
        hl.addWidget(self._spark)

    def update_text(self, text):
        self._label.setText(text)

    def push(self, name, val=None):
        if val is None:
            self._spark.push(name, name)
        else:
            self._spark.push(name, val)


# ─────────────────────────────────────────────
# 主监控组件
# ─────────────────────────────────────────────
class SysMonitorWidget(QWidget):
    """系统资源监控面板，纯黑背景居中显示。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        # v22: 恢复黑底高对比度（v21.2 原始风格）
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "SysMonitorWidget { background-color: #0a0a0a; }"
            "SysMonitorWidget QLabel { color: #ffffff; background-color: #0a0a0a; }"
        )

        hl = QHBoxLayout(self)
        hl.setContentsMargins(8, 0, 8, 0)
        hl.setSpacing(12)

        # 颜色方案：CPU=青, MEM=绿, NET 下行=橙/上行=黄, DSK 读=紫/写=粉
        self._cpu = _MetricBlock("CPU --", dict(colors=[("cpu", "#00e5ff")]))
        self._mem = _MetricBlock("MEM --", dict(colors=[("mem", "#69f0ae")]))
        self._net = _MetricBlock("NET --", dict(
            colors=[("rx", "#ff9100"), ("tx", "#ffd54f")]))
        self._disk = _MetricBlock("DSK --", dict(
            colors=[("read", "#b388ff"), ("write", "#ff80ab")]))

        hl.addStretch(1)
        hl.addWidget(self._cpu)
        hl.addWidget(self._mem)
        hl.addWidget(self._net)
        hl.addWidget(self._disk)
        hl.addStretch(1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)  # 1s 间隔

    def _tick(self):
        try:
            data = sys_monitor.snapshot()
        except Exception:
            return

        # CPU
        cpu = data["cpu"]
        self._cpu.update_text(f"CPU {cpu:.0f}%")
        self._cpu.push("cpu", cpu)

        # 内存
        mp = data["mem_pct"]
        used = data["mem_used"]
        total = data["mem_total"]
        if total > 0:
            self._mem.update_text(f"MEM {mp:.0f}% {used:.1f}/{total:.0f}G")
            self._mem.push("mem", mp)
        else:
            self._mem.update_text("MEM --")

        # 网络：下行(橙) + 上行(黄)
        rx = data["net_rx_kbs"]
        tx = data["net_tx_kbs"]
        rx_s = f"{rx:.0f}K" if rx < 1000 else f"{rx/1024:.1f}M"
        tx_s = f"{tx:.0f}K" if tx < 1000 else f"{tx/1024:.1f}M"
        self._net.update_text(f"NET ↓{rx_s}  ↑{tx_s}")
        self._net.push("rx", rx)
        self._net.push("tx", tx)

        # 磁盘：读(紫) + 写(粉)
        dr = data["disk_r_kbs"]
        dw = data["disk_w_kbs"]
        dr_s = f"{dr:.0f}K" if dr < 1000 else f"{dr/1024:.1f}M"
        dw_s = f"{dw:.0f}K" if dw < 1000 else f"{dw/1024:.1f}M"
        self._disk.update_text(f"DSK R{dr_s}  W{dw_s}")
        self._disk.push("read", dr)
        self._disk.push("write", dw)
