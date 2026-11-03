# -*- coding: utf-8 -*-
"""打包前运行时数据快照：dist\Cube Setlist Manager 的 config/playlist
追加到 _bak_dist（带时间戳，永不覆盖）。"""
import os
import shutil
import time

# (dist 子目录, 数据文件)
PRODUCTS = (
    ("Cube Setlist Manager", ("config.json", "playlist.json")),
)


def main():
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = "_bak_dist"
    os.makedirs(out, exist_ok=True)
    for sub, files in PRODUCTS:
        for f in files:
            p = os.path.join("dist", sub, f)
            if os.path.exists(p):
                shutil.copy2(p, os.path.join(out, ts + "_" + f))
                print("已快照:", os.path.join(out, ts + "_" + f))


if __name__ == "__main__":
    main()
