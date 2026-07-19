# -*- coding: utf-8 -*-
"""
托盘图标管理器（v23）：加载 SVG 图标集并管理动画状态。

状态轮换：
  - idle       → 墨镜 sunglasses.svg
  - scanning   → 放大镜 magnify_1~3.svg 循环（扫描线微动）
  - processing → 齿轮 gear_1~6.svg 循环（旋转）

定时器在主线程（MainWindow）中驱动，frame_interval 可调。
"""

import os
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import QTimer


_ICON_DIR = os.path.join(os.path.dirname(__file__), "..", "resources", "icons")


class TrayIconManager:
    """管理托盘图标状态与动画。"""

    def __init__(self, tray_icon):
        self.tray = tray_icon
        self._timer = QTimer()
        self._timer.timeout.connect(self._next_frame)

        self._state = "idle"
        self._frame = 0
        self._frames = {}  # state -> [QIcon, ...]

        # 预加载所有图标
        self._load("idle", ["sunglasses.svg"])
        self._load("scanning", ["magnify_1.svg", "magnify_2.svg", "magnify_3.svg"])
        self._load("processing", [f"gear_{i}.svg" for i in range(1, 7)])

        self._set("idle")

    def _load(self, name, filenames):
        icons = []
        for fn in filenames:
            path = os.path.join(_ICON_DIR, fn)
            if os.path.exists(path):
                icons.append(QIcon(path))
            else:
                icons.append(QIcon())  # 空兜底
        self._frames[name] = icons

    def _set(self, name):
        icons = self._frames.get(name, [])
        if icons:
            self.tray.setIcon(icons[0])
        self._state = name
        self._frame = 0

    def _next_frame(self):
        icons = self._frames.get(self._state, [])
        if len(icons) > 1:
            self._frame = (self._frame + 1) % len(icons)
            self.tray.setIcon(icons[self._frame])

    def set_state(self, name):
        """切换状态：idle / scanning / processing"""
        if name == self._state and self._timer.isActive():
            return  # 已在目标状态
        self._timer.stop()
        interval = 0
        if name == "idle":
            interval = 0  # 静态
        elif name == "scanning":
            interval = 400  # 400ms/帧
        elif name == "processing":
            interval = 250  # 250ms/帧
        self._set(name)
        if interval > 0 and len(self._frames.get(name, [])) > 1:
            self._timer.start(interval)

    def stop_animation(self):
        self._timer.stop()
