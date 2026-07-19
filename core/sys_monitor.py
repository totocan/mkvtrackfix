# -*- coding: utf-8 -*-
"""
系统资源监控数据层（v21.2：CPU / 内存 / 网络 / 磁盘）。
"""
import time

# ─────────────────────────────────────────────
# psutil
# ─────────────────────────────────────────────
_HAS_PSUTIL = False
_psutil = None
try:
    import psutil as _psutil
    _HAS_PSUTIL = True
except ImportError:
    pass


# ─────────────────────────────────────────────
# CPU
# ─────────────────────────────────────────────
def get_cpu():
    """返回 CPU 使用率 (0-100)。"""
    if _HAS_PSUTIL:
        return _psutil.cpu_percent(interval=None)
    return 0.0


# ─────────────────────────────────────────────
# 内存
# ─────────────────────────────────────────────
def get_mem():
    """返回 (used_gb, total_gb, percent)。"""
    if _HAS_PSUTIL:
        mem = _psutil.virtual_memory()
        return mem.used / (1024**3), mem.total / (1024**3), mem.percent
    return 0.0, 0.0, 0.0


# ─────────────────────────────────────────────
# 网络
# ─────────────────────────────────────────────
_net_prev = None
_net_ts = None


def get_net():
    """返回 (rx_kbs, tx_kbs)。"""
    global _net_prev, _net_ts
    if _HAS_PSUTIL:
        now = _psutil.net_io_counters()
        rx, tx = now.bytes_recv, now.bytes_sent
        if _net_prev is None:
            _net_prev = (rx, tx)
            _net_ts = time.time()
            return (0.0, 0.0)
        dt = time.time() - _net_ts
        if dt < 0.01:
            return (0.0, 0.0)
        rx_kbs = max(0, (rx - _net_prev[0]) / dt / 1024)
        tx_kbs = max(0, (tx - _net_prev[1]) / dt / 1024)
        _net_prev = (rx, tx)
        _net_ts = time.time()
        return (rx_kbs, tx_kbs)
    return (0.0, 0.0)


# ─────────────────────────────────────────────
# 磁盘 I/O（v21.2 新增，替代 GPU）
# ─────────────────────────────────────────────
_disk_prev = None
_disk_ts = None


def get_disk():
    """返回 (read_kbs, write_kbs)。"""
    global _disk_prev, _disk_ts
    if _HAS_PSUTIL:
        now = _psutil.disk_io_counters()
        r, w = now.read_bytes, now.write_bytes
        if _disk_prev is None:
            _disk_prev = (r, w)
            _disk_ts = time.time()
            return (0.0, 0.0)
        dt = time.time() - _disk_ts
        if dt < 0.01:
            return (0.0, 0.0)
        r_kbs = max(0, (r - _disk_prev[0]) / dt / 1024)
        w_kbs = max(0, (w - _disk_prev[1]) / dt / 1024)
        _disk_prev = (r, w)
        _disk_ts = time.time()
        return (r_kbs, w_kbs)
    return (0.0, 0.0)


# ─────────────────────────────────────────────
# 综合快照
# ─────────────────────────────────────────────
def snapshot():
    """一次性取所有数据，返回 dict。"""
    cpu = get_cpu()
    used, total, pct = get_mem()
    rx, tx = get_net()
    dr, dw = get_disk()
    return {
        "cpu": cpu,
        "mem_pct": pct,
        "mem_used": used,
        "mem_total": total,
        "net_rx_kbs": rx,
        "net_tx_kbs": tx,
        "disk_r_kbs": dr,
        "disk_w_kbs": dw,
    }
