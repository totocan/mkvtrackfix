# -*- coding: utf-8 -*-
"""Pack the current portable installation into a standalone offline zip.

Used by backup.bat (personal backup) and share.bat (distribute to others).
Both keep the package fully self-contained: source + portable Python +
AI model + native tools, so the recipient runs run.bat with no internet.
"""
import os
import zipfile


# Directories never packed (ephemeral / regenerable / not for distribution)
EXCLUDE_DIRS = {"__pycache__", "logs", ".git", "tmp", "assets"}
# File extensions never packed (archives would self-recurse)
EXCLUDE_EXT = {".zip", ".pptx", ".pyc"}


def _iter_files(root, exclude_config):
    for dp, dirs, fns in os.walk(root):
        # prune excluded dirs in place so os.walk won't descend
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for fn in fns:
            if fn.endswith(".pyc"):
                continue
            if fn.lower().endswith(".zip") or fn.lower().endswith(".pptx"):
                continue
            if exclude_config and fn == "config.json":
                # don't leak the sharer's absolute tool paths
                continue
            yield os.path.join(dp, fn)


def pack(root, out_zip, exclude_config=False):
    """Pack the portable install at `root` into `out_zip`."""
    count = 0
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for full in _iter_files(root, exclude_config):
            arc = os.path.relpath(full, root)
            z.write(full, arc)
            count += 1
    return count


def backup(root, out_zip):
    """Personal backup: include everything, including user config.json."""
    return pack(root, out_zip, exclude_config=False)


def share(root, out_zip):
    """Shareable package: exclude user-specific config.json (recipients
    auto-detect tools in their own tools/ directory)."""
    return pack(root, out_zip, exclude_config=True)


if __name__ == "__main__":
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    mode = sys.argv[1] if len(sys.argv) > 1 else "backup"
    out = sys.argv[2] if len(sys.argv) > 2 else "pack.zip"
    fn = share if mode == "share" else backup
    n = fn(here, out)
    print(f"Packed {n} files -> {out}")
