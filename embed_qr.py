# -*- coding: utf-8 -*-
"""
公众号二维码嵌入脚本

把真正的公众号二维码图片转为 base64 嵌入到 settings_dialog.py。

用法：
  1. 把你的公众号二维码图片保存到 tools/qrcode.png
  2. 运行：python embed_qr.py
  3. 自动替换 settings_dialog.py 中的占位 base64
"""
import base64
import re
from pathlib import Path

QR_FILE = Path(__file__).parent / "tools" / "qrcode.png"
TARGET = Path(__file__).parent / "gui" / "settings_dialog.py"
PLACEHOLDER = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="

if not QR_FILE.exists():
    print(f"❌ 找不到二维码文件: {QR_FILE}")
    print(f"请把你的公众号二维码图片保存到 {QR_FILE}，再重新运行本脚本")
    raise SystemExit(1)

data = QR_FILE.read_bytes()
b64 = base64.b64encode(data).decode()
print(f"✓ 读取 {QR_FILE} ({len(data)} 字节)")
print(f"  base64 长度: {len(b64)}")

content = TARGET.read_text(encoding="utf-8")
# 替换占位
if PLACEHOLDER in content:
    new_content = content.replace(PLACEHOLDER, b64)
    TARGET.write_text(new_content, encoding="utf-8")
    print(f"✓ 已替换 {TARGET} 中的占位 base64")
else:
    print(f"⚠ 占位 base64 未在 {TARGET} 中找到")
    print("  请检查 settings_dialog.py 中是否已包含 _WECHAT_QR_B64 = '...' 占位")
