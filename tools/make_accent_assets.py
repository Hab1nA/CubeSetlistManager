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
# 精灵图底色与官方 accent 纯色（色相重映射的锚点）
BASE = (28, 28, 28)
ACCENT = (0x57, 0xC8, 0xFF)
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def hue_of(hexstr):
    r, g, b = (int(hexstr[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return h, s, v


def main():
    import sv_ttk
    sp = os.path.dirname(sv_ttk.__file__)
    sheet = Image.open(os.path.join(sp, "theme", "spritesheet_dark.png"))
    sheet = sheet.convert("RGBA")
    out_dir = os.path.join(HERE, "assets")
    os.makedirs(out_dir, exist_ok=True)
    for target, hexstr in TARGETS.items():
        th = hue_of(hexstr)[0]
        for state, (x, y) in SPRITES.items():
            img = sheet.crop((x, y, x + 20, y + 20))
            if state != "dis":   # dis 本身灰色不换色
                px = img.load()
                # 每个像素 = α×accent纯色 + (1-α)×底色 的混合。按 G 通道
                # 解 α（accent G=200、底色 G=28，区分度最大），accent 分量
                # 的 hue 换成目标语义色、S/V 保留，再按 α 回混——渐变边缘
                # 与普通钮的灰渐变完全同构，不再出现着色区外扩 1px（整图
                # hue 平移会把边缘混合像素也染色，形状外扩）
                acc_g, base_g = ACCENT[1], BASE[1]
                for j in range(20):
                    for i in range(20):
                        r, g, b, a = px[i, j]
                        if a == 0:
                            continue
                        alpha = max(0.0, min(1.0, (g - base_g)
                                             / (acc_g - base_g)))
                        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255,
                                                      b / 255)
                        r2, g2, b2 = colorsys.hsv_to_rgb(th, s, v)
                        acc2 = (r2 * 255, g2 * 255, b2 * 255)
                        px[i, j] = tuple(round(alpha * acc2[k]
                                               + (1 - alpha) * BASE[k])
                                         for k in range(3)) + (a,)
            img.save(os.path.join(out_dir, "btn_%s_%s.png" % (target,
                                                              state)))
    print("generated:", sorted(f for f in os.listdir(out_dir)
                               if f.startswith("btn_")))


if __name__ == "__main__":
    main()
