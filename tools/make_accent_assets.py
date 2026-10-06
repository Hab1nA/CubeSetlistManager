# -*- coding: utf-8 -*-
"""强调按钮资产生成：从 sv-ttk 官方精灵图切出 accent 按钮各状态子图，
色相重映射（蓝→绿/红，保留 S/V 与抗锯齿），写入 assets/btn_*_*.png。
一次性工具：sv-ttk 精灵图升级或强调色调整时重跑。
用法：python tools/make_accent_assets.py
"""
import colorsys
import os

from PIL import Image

SPRITES = {  # 来自 sv_ttk sprites_dark.tcl 的 accent 子图坐标
    "rest": (80, 144), "pressed": (60, 144), "dis": (132, 118),
    "hover": (40, 152), "focus": (20, 152), "focus-hover": (0, 152),
}
TARGETS = {"start": "#5ad469", "stop": "#a03030"}
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def hue_of(hexstr):
    r, g, b = (int(hexstr[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hsv(r, g, b)[0]


def main():
    import sv_ttk
    sp = os.path.dirname(sv_ttk.__file__)
    sheet = Image.open(os.path.join(sp, "theme", "spritesheet_dark.png"))
    sheet = sheet.convert("RGBA")
    out_dir = os.path.join(HERE, "assets")
    os.makedirs(out_dir, exist_ok=True)
    for target, hexstr in TARGETS.items():
        th = hue_of(hexstr)
        for state, (x, y) in SPRITES.items():
            img = sheet.crop((x, y, x + 20, y + 20))
            if state != "dis":   # dis 本身灰色不换色
                px = img.load()
                for j in range(20):
                    for i in range(20):
                        r, g, b, a = px[i, j]
                        if a == 0:
                            continue
                        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255,
                                                      b / 255)
                        if s > 0.25 and 0.47 < h < 0.68:  # 只换蓝色系 hue
                            r2, g2, b2 = colorsys.hsv_to_rgb(th, s, v)
                            px[i, j] = (round(r2 * 255), round(g2 * 255),
                                        round(b2 * 255), a)
            img.save(os.path.join(out_dir, "btn_%s_%s.png" % (target,
                                                              state)))
    print("generated:", sorted(f for f in os.listdir(out_dir)
                               if f.startswith("btn_")))


if __name__ == "__main__":
    main()
