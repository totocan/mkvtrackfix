# -*- coding: utf-8 -*-
"""
右下角系统托盘监控（独立小程序，不依赖主 GUI 进程）。

功能（对应需求：运行程序 / 结束程序 / 退出本身 / 查看最新日志）：

  - 运行程序  : 直接拉起 python main.py（拿到子进程 PID，便于精确结束）。
  - 结束程序  : terminate() 子进程（主 GUI 会正常退出；若卡死则 killed）。
  - 查看日志  : 打开 logs/ 下最新的 mmf_<时间戳>.log。
  - 退出本身  : 退出托盘；若主程序仍在运行，会询问是否一并结束。

托盘图标用代码绘制（蓝底白字 "MM"），不依赖任何外部图片资源。
绿色包下用 python\\python.exe 启动；普通环境用系统 python。

运行：python tray_monitor.py   （或双击 tray.bat）
"""
import os
import sys
import subprocess

from PyQt5.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QAction, QMessageBox,
)
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor, QFont, QDesktopServices
from PyQt5.QtCore import Qt, QUrl, QTimer


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
def _app_root():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _python_exe(app_root):
    """优先绿色包内的 python\\python.exe，否则回退到当前解释器。"""
    for cand in ("python/python.exe", "python.exe"):
        p = os.path.join(app_root, cand)
        if os.path.exists(p):
            return p
    return sys.executable


def _latest_log(app_root):
    log_dir = os.path.join(app_root, "logs")
    if not os.path.isdir(log_dir):
        return None
    logs = [
        os.path.join(log_dir, f)
        for f in os.listdir(log_dir)
        if f.startswith("mmf_") and f.endswith(".log")
    ]
    if not logs:
        return None
    return max(logs, key=os.path.getmtime)


# ---------------------------------------------------------------------------
# 图标生成（代码绘制，无外部资源，支持 emoji + 自动降级）
# ---------------------------------------------------------------------------
def _make_icon(char="MM", bg="#2d6cdf", fallback="MM"):
    """生成图标 QIcon。

    char:   优先使用的字符（可以为 emoji）
    bg:     背景色
    fallback: emoji 渲染失败时的备选文字
    """
    size = 64
    pm = QPixmap(size, size)
    pm.fill(QColor(bg))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor("white"))
    # 先试 emoji 字体
    font = QFont("Segoe UI Emoji", size // 2)
    p.setFont(font)
    p.drawText(pm.rect(), Qt.AlignCenter, char)
    p.end()

    # 检测 emoji 是否渲染成功：取中心 3x3 采样点
    img = pm.toImage()
    bg_color = QColor(bg)
    rendered = False
    sample_points = [(size//2, size//2),          # 正中
                     (size//2-8, size//2-8),      # 左上
                     (size//2+8, size//2-8),      # 右上
                     (size//2-8, size//2+8),      # 左下
                     (size//2+8, size//2+8)]      # 右下
    for x, y in sample_points:
        if 0 <= x < size and 0 <= y < size:
            if img.pixelColor(x, y) != bg_color:
                rendered = True
                break

    if not rendered and fallback:
        # emoji 没渲染出来（显示为方框/空白），fallback 到文字
        pm.fill(QColor(bg))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QColor("white"))
        p.setFont(QFont("Arial", size // 3, QFont.Bold))
        p.drawText(pm.rect(), Qt.AlignCenter, fallback)
        p.end()

    return QIcon(pm)


# ---------------------------------------------------------------------------
# 托盘应用
# ---------------------------------------------------------------------------
class TrayApp:
    def __init__(self):
        self.app_root = _app_root()
        self.proc = None  # subprocess.Popen of main.py

        self.app = QApplication(sys.argv)
        self.app.setApplicationName("MediaMetaFixer Monitor")
        # 避免无父窗口时的警告
        self.app.setQuitOnLastWindowClosed(False)

        if not QSystemTrayIcon.isSystemTrayAvailable():
            QMessageBox.critical(
                None, "MediaMetaFixer 监控",
                "当前系统不可用系统托盘（右下角图标）。\n"
                "请在支持系统托盘的环境运行，或直接双击 main.py 启动主程序。")
            sys.exit(1)

        self.icon = _make_icon("🎯", bg="#2d6cdf", fallback="影")
        self.tray = QSystemTrayIcon(self.icon)
        self.tray.setToolTip("MediaMetaFixer 监控 - 未运行")

        # v22: 叠加 SVG 图标 + 文件轮询 IPC（整个块兜底，任何异常不阻止托盘显示）
        try:
            self._svg_icons = {}
            self._svg_state = "idle"
            self._svg_frame = 0
            self._svg_timer = QTimer()
            self._svg_timer.timeout.connect(self._svg_next_frame)
            svg_dir = os.path.join(self.app_root, "resources", "icons")
            if os.path.isdir(svg_dir):
                for state, files in [
                    ("idle", ["sunglasses.svg"]),
                    ("scanning", ["magnify_1.svg", "magnify_2.svg", "magnify_3.svg"]),
                    ("processing", [f"gear_{i}.svg" for i in (1,2,3,4,5,6)]),
                    ("done", ["done.svg"]),
                ]:
                    icons = [QIcon(os.path.join(svg_dir, fn)) for fn in files
                             if os.path.exists(os.path.join(svg_dir, fn))]
                    if icons:
                        self._svg_icons[state] = icons
                if self._svg_icons.get("idle"):
                    self.tray.setIcon(self._svg_icons["idle"][0])

            self._status_path = os.path.join(self.app_root, "tmp", "tray_status.txt")
            self._last_status = ""
            self._poll_timer = QTimer()
            self._poll_timer.timeout.connect(self._poll_status)
            self._poll_timer.start(500)
        except Exception:
            pass

        self._build_menu()
        self.tray.show()
        self._refresh()

    # v22: SVG 状态图标切换 + 动画
    def _svg_next_frame(self):
        icons = self._svg_icons.get(self._svg_state, [])
        if len(icons) > 1:
            self._svg_frame = (self._svg_frame + 1) % len(icons)
            self.tray.setIcon(icons[self._svg_frame])

    def _svg_set_state(self, state):
        if not hasattr(self, '_svg_icons') or state not in self._svg_icons:
            return
        if hasattr(self, '_svg_timer'):
            self._svg_timer.stop()
        self._svg_state = state
        self._svg_frame = 0
        icons = self._svg_icons[state]
        if icons:
            self.tray.setIcon(icons[0])
        interval = {"scanning": 400, "processing": 250}.get(state, 0)
        if hasattr(self, '_svg_timer') and interval > 0 and len(icons) > 1:
            self._svg_timer.start(interval)

    # v22: 文件轮询 IPC — 读取主程序写入的状态文件
    def _poll_status(self):
        try:
            with open(self._status_path, encoding="utf-8") as f:
                status = f.read().strip()
        except Exception:
            return
        if status and status != self._last_status:
            self._last_status = status
            if status == "done":
                # 任务完成：显示对勾 3 秒 + 提示音
                self._svg_set_state("done")
                self._beep_done()
                if hasattr(self, "_done_timer"):
                    self._done_timer.stop()
                self._done_timer = QTimer()
                self._done_timer.setSingleShot(True)
                self._done_timer.timeout.connect(lambda: self._svg_set_state("idle"))
                self._done_timer.start(3000)
            elif status in ("idle", "scanning", "processing"):
                if hasattr(self, "_done_timer"):
                    self._done_timer.stop()
                self._svg_set_state(status)

    def _beep_done(self):
        """任务完成提示音。"""
        try:
            if sys.platform == "win32":
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            self.app.beep()

    # ----------------------------- 菜单 -----------------------------
    def _build_menu(self):
        self.menu = QMenu()

        self.a_run = QAction("运行程序", self.menu)
        self.a_run.triggered.connect(self.run_program)
        self.a_stop = QAction("结束程序", self.menu)
        self.a_stop.triggered.connect(self.stop_program)
        self.a_log = QAction("查看最新日志", self.menu)
        self.a_log.triggered.connect(self.open_log)
        self.a_quit = QAction("退出本身", self.menu)
        self.a_quit.triggered.connect(self.quit_self)

        self.menu.addAction(self.a_run)
        self.menu.addAction(self.a_stop)
        self.menu.addSeparator()
        self.menu.addAction(self.a_log)
        self.menu.addSeparator()
        self.menu.addAction(self.a_quit)

        self.tray.setContextMenu(self.menu)
        # 单击/双击托盘图标 = 查看日志
        # v22: 点击托盘图标不做任何操作，用户通过右键菜单选择
        # self.tray.activated.connect(self._on_activate)  # 注释掉，默认无动作

    def _on_activate(self, reason):
        if reason in (QSystemTrayIcon.DoubleClick,
                      QSystemTrayIcon.Trigger):
            self.open_log()

    # ----------------------------- 动作 -----------------------------
    def run_program(self):
        if self.proc is not None and self.proc.poll() is None:
            QMessageBox.information(None, "提示", "主程序已在运行中。")
            return
        py = _python_exe(self.app_root)
        try:
            # 直接拉起 python main.py：子进程即主 GUI，PID 可控，便于精确结束
            self.proc = subprocess.Popen(
                [py, "main.py"],
                cwd=self.app_root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x00000200,  # CREATE_NEW_PROCESS_GROUP
            )
            self._info(f"已启动主程序 (pid={self.proc.pid})。")
        except Exception as e:
            self._info(f"启动失败：{e}")
        self._refresh()

    def stop_program(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self._info("已向主程序发送结束信号。")
            except Exception as e:
                self._info(f"结束失败：{e}")
        else:
            self._info("主程序未运行。")
        self._refresh()

    def open_log(self):
        lp = _latest_log(self.app_root)
        if not lp:
            QMessageBox.information(None, "提示", "暂无日志文件。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(lp)))

    def quit_self(self):
        if self.proc is not None and self.proc.poll() is None:
            r = QMessageBox.question(
                None, "确认退出",
                "主程序仍在运行，退出监控会一并结束主程序吗？",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return
            if r == QMessageBox.Yes:
                self._force_kill()
        self.tray.hide()
        self.app.quit()

    def _force_kill(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.kill()
            except Exception:
                pass

    def _refresh(self):
        running = bool(self.proc is not None and self.proc.poll() is None)
        self.a_stop.setEnabled(running)
        self.tray.setToolTip(
            "MediaMetaFixer 监控 - " + ("运行中" if running else "未运行"))

    def _info(self, msg):
        try:
            # v22: 通知用当前状态图标（墨镜/对勾等），不是后备箭靶
            icons = self._svg_icons.get(self._svg_state, [])
            cur_icon = icons[0] if icons else self.icon
            self.tray.showMessage("MediaMetaFixer 监控", msg, cur_icon, 3000)
        except Exception:
            pass

    def run(self):
        sys.exit(self.app.exec_())


if __name__ == "__main__":
    TrayApp().run()
