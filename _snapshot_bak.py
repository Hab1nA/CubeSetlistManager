# -*- coding: utf-8 -*-
"""打包前运行时数据快照：config/playlist 追加到 _bak_dist（带时间戳，永不覆盖）。"""
import os
import shutil
import time


def main():
    ts = time.strftime("%Y%m%d_%H%M%S")
    src = os.path.join("dist", "Cube Setlist Manager")
    out = "_bak_dist"
    os.makedirs(out, exist_ok=True)
    for f in ("config.json", "playlist.json"):
        p = os.path.join(src, f)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(out, ts + "_" + f))
            print("已快照:", os.path.join(out, ts + "_" + f))


if __name__ == "__main__":
    main()
