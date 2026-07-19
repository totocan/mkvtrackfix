# -*- coding: utf-8 -*-
"""下载 faster-whisper 模型到 models/<size>/，实现离线运行。

默认从魔塔社区（ModelScope，国内更快更稳）下载；失败自动回退 HuggingFace。

用法：
    python download_model.py medium                 # 默认（魔塔）
    python download_model.py large-v3 --source huggingface
    python download_model.py medium --source modelscope
"""
import argparse
import os
import subprocess
import sys


def _target(size):
    here = os.path.dirname(os.path.abspath(__file__))
    target = os.path.join(here, "models", size)
    os.makedirs(target, exist_ok=True)
    return target


def download_modelscope(size, target):
    try:
        from modelscope import snapshot_download
    except ImportError:
        print("未安装 modelscope，正在自动安装（仅首次）...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "modelscope"])
        from modelscope import snapshot_download
    repo_id = f"gpustack/faster-whisper-{size}"
    print(f"从魔塔社区下载 {repo_id} -> {target}")
    print("（仅首次需要联网；之后代码会从本地 models/ 离线加载）")
    snapshot_download(repo_id, local_dir=target, local_dir_use_symlinks=False)


def download_huggingface(size, target):
    from huggingface_hub import snapshot_download
    repo_id = f"Systran/faster-whisper-{size}"
    print(f"从 HuggingFace 下载 {repo_id} -> {target}")
    print("（仅首次需要联网；之后代码会从本地 models/ 离线加载）")
    snapshot_download(repo_id, local_dir=target, local_dir_use_symlinks=False)


def main():
    parser = argparse.ArgumentParser(description="下载 faster-whisper 模型")
    parser.add_argument("size", nargs="?", default="medium",
                        help="模型尺寸：tiny/base/small/medium/large-v3")
    parser.add_argument("--source", default="modelscope",
                        choices=["modelscope", "huggingface"],
                        help="下载源（默认 modelscope，国内更快更稳）")
    args = parser.parse_args()

    target = _target(args.size)
    # 主源优先，失败回退另一源
    order = ([args.source] +
             (["huggingface"] if args.source == "modelscope"
              else ["modelscope"]))

    for src in order:
        try:
            if src == "modelscope":
                download_modelscope(args.size, target)
            else:
                download_huggingface(args.size, target)
            print("模型下载完成。")
            return
        except Exception as e:
            print(f"[{src}] 下载失败：{e}")
            print("尝试下一个源...")
    print("所有下载源均失败，请检查网络或手动下载模型到 models/ 目录。")
    sys.exit(1)


if __name__ == "__main__":
    main()
