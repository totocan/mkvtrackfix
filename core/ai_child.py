# -*- coding: utf-8 -*-
"""
AI 识别子进程主体。由 core.ai_worker.AIDetector 通过
`python -m core.ai_child` 启动（cwd=项目根目录，故 core 包可被导入）。

协议（标准流 + JSON 行，UTF-8）：
  1) 父进程先写一行 JSON 作为配置：
       {"model_size":.., "device":.., "compute_type":.., "cpu_threads":.., ...}
  2) 子进程加载模型，向 stdout 写：
       {"t":"ready", "model_size":.., "local":..}      成功
       {"t":"load_error", "e":.., "tb":..}              加载/import 失败
  3) 之后父进程每行发一个识别请求：
       {"id":N, "wav":"/path/x.wav", "title":".."}
     子进程每行回：
       {"t":"ok",   "id":N, "out":{...}}               识别成功
       {"t":"err",  "id":N, "e":.., "tb":..}           单文件识别异常（进程仍存活）
  4) 原生输出（ctranslate2 / onnxruntime 的 C/C++ 日志）走 stderr，
     由父进程实时捕获并写入结构化日志，解决「控制台一大堆、日志里啥都没有」。
  5) 父进程关闭 stdin 即代表退出，子进程读到 EOF 自然结束。
"""
import sys
import json
import traceback


def _write(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _err(m):
    try:
        sys.stderr.write(str(m) + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def _child_main():
    # —— 1) import 阶段（faster_whisper 缺失 / 异常也会走到这里）——
    try:
        from faster_whisper import WhisperModel
        from core import audio_detect as ad
    except Exception as e:
        _err(f"[AI-child] import 失败: {e}")
        _write({"t": "load_error", "e": repr(e),
                "tb": traceback.format_exc()})
        return

    # —— 2) 读取父进程发来的配置（第一行）——
    try:
        raw = sys.stdin.readline()
        cfg = json.loads(raw) if raw.strip() else {}
    except Exception as e:
        _err(f"[AI-child] 读取配置失败: {e}")
        _write({"t": "load_error", "e": repr(e),
                "tb": traceback.format_exc()})
        return

    model_size = cfg.get("model_size", "medium")
    device = cfg.get("device", "cpu")
    compute_type = cfg.get("compute_type", "int8")
    cpu_threads = int(cfg.get("cpu_threads", 0))

    local = ad._local_model_path(model_size)
    model_arg = local if local else model_size
    _err(f"[AI-child] 加载模型 {model_size} "
         f"(device={device}, compute_type={compute_type}, "
         f"source={'本地' if local else '在线/缓存'}) ...")
    try:
        model = WhisperModel(
            model_arg, device=device,
            compute_type=compute_type, cpu_threads=cpu_threads or 0)
    except Exception as e:
        _err(f"[AI-child] 模型加载失败: {e}")
        _write({"t": "load_error", "e": repr(e),
                "tb": traceback.format_exc()})
        return

    _write({"t": "ready", "model_size": model_size, "local": bool(local)})
    _err("[AI-child] 模型就绪，进入识别循环")

    # —— 3) 请求循环 ——
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        rid = req.get("id")
        wav_path = req.get("wav")
        title_hint = req.get("title")
        try:
            out = ad._detect_with_model(model, wav_path, title_hint, cfg)
            _write({"t": "ok", "id": rid, "out": out})
        except Exception as e:
            _write({"t": "err", "id": rid, "e": repr(e),
                    "tb": traceback.format_exc()})


if __name__ == "__main__":
    _child_main()
