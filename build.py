# -*- coding: utf-8 -*-
"""
打包为独立 Windows exe：  python build.py

前置：
  1) pip install pyinstaller
  2) 仍需单独安装原生程序：
     - mkvtoolnix (提供 mkvmerge)   https://mkvtoolnix.download/
     - Tesseract OCR (处理 sup/PGS 图像字幕)  https://github.com/UB-Mannheim/tesseract/wiki
     （程序会自动探测其路径，也可在“设置”里手动指定）

说明：
  - 默认 --onefile --windowed：产出单个 MediaMetaFixer.exe，双击即用。
  - 若嫌 --onefile 启动慢，可把下面 --onefile 改成 --onedir（同目录散开，启动更快）。
  - AI 模型在首次识别时自动联网下载到用户缓存，无需打包进 exe。
  - 打包后配置(config.json)默认写在 exe 同目录；若目录不可写(如 Program Files)，
    自动改用 %APPDATA%\\MediaMetaFixer。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", "MediaMetaFixer",
        "--onefile",          # 单文件 exe；如改 --onedir 则启动更快
        "--windowed",         # 无控制台窗口（纯 GUI）
        "--clean",
        "--noconfirm",
        # —— 隐藏依赖（这些包有动态导入，需显式收集）——
        "--hidden-import", "faster_whisper",
        "--hidden-import", "ctranslate2",
        "--hidden-import", "onnxruntime",
        "--hidden-import", "av",
        "--hidden-import", "langdetect",
        "--collect-submodules", "faster_whisper",
        "--collect-submodules", "ctranslate2",
        "--collect-submodules", "av",
        "--collect-submodules", "langdetect",
        os.path.join(HERE, "main.py"),
    ]
    print("执行：\n  " + " ".join(cmd))
    subprocess.check_call(cmd)
    print("\n完成。exe 位于 dist\\MediaMetaFixer.exe")


if __name__ == "__main__":
    main()
