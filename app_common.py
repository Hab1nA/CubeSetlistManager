# -*- coding: utf-8 -*-
"""两窗（setlist_gui / automator_gui）共用无 UI 基础设施：DAW exe 探测与
默认路径、工程库扫描、config IO 三件套（损坏隔离/原子写+fsync/滚动备份）、
异常格式化。

教训：约 24 对复制粘贴的基础设施里「修复只落一边」已实锤两次（MIDI 口
dead 热拔重开、config 损坏防护）——凡两窗语义相同的基础设施一律落此处，
GUI 层只留差异（daw_settings 两窗规则不同，仍各自持有）。"""
import json
import os
import pathlib
import re
import shutil
import sys

from obs_ctrl import natural_key

if getattr(sys, "frozen", False):   # PyInstaller exe：数据文件放 exe 同目录
    HERE = pathlib.Path(sys.executable).resolve().parent
else:
    HERE = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
# 滚动备份/损坏隔离文件：全部字面量常量（扫描器可静态判定无穿越面）
CONFIG_BAKS = (HERE / "config.json.bak", HERE / "config.json.bak2",
               HERE / "config.json.bak3")
CONFIG_CORRUPT = HERE / "config.json.corrupt"


def find_cubase_exe():
    r"""扫 Program Files\Steinberg 各版本目录，取版本号最高的 Cubase<N>.exe
    （N 取自 exe 文件名，兼容「Cubase Pro 13」式目录名；升级 Cubase 无需改路径）。"""
    base = r"C:\Program Files\Steinberg"
    try:
        dirs = os.listdir(base)
    except OSError:
        return None
    best = None
    for d in dirs:
        if "cubase" not in d.lower():
            continue
        try:
            exes = os.listdir(os.path.join(base, d))
        except OSError:
            continue
        for e in exes:
            m = re.fullmatch(r"Cubase(\d+)\.exe", e)
            if m and (best is None or int(m.group(1)) > best[0]):
                best = (int(m.group(1)), os.path.join(base, d, e))
    return best[1] if best else None


def find_s1_exe():
    r"""扫 Program Files\PreSonus 各版本目录，取版本号最高的 Studio One.exe
    （版本号取自目录名，如「Studio One 7」；升级无需改路径）。"""
    base = r"C:\Program Files\PreSonus"
    try:
        dirs = os.listdir(base)
    except OSError:
        return None
    best = None
    for d in dirs:
        if "studio one" not in d.lower():
            continue
        try:
            if "Studio One.exe" not in os.listdir(os.path.join(base, d)):
                continue
        except OSError:
            continue
        m = re.search(r"(\d+)", d)
        v = int(m.group(1)) if m else 0
        if best is None or v > best[0]:
            best = (v, os.path.join(base, d, "Studio One.exe"))
    return best[1] if best else None

# 各底座的默认路径（exe 探测失败时的兜底 + 全新配置的工程库默认值）。
# root 用 %USERPROFILE%\Documents 推导：旧硬编码个人路径在他机无效，本机
# 结果不变（C:\Users\XKZ\Documents\…）
_USER_DOCS = pathlib.Path(os.path.expanduser("~")) / "Documents"
DAW_DEFAULTS = {
    "cubase": {"exe": (find_cubase_exe()
                       or r"C:\Program Files\Steinberg\Cubase 15\Cubase15.exe"),
               "root": str(_USER_DOCS / "Cubase Projects")},
    "studioone": {"exe": (find_s1_exe()
                          or r"C:\Program Files\PreSonus\Studio One 7"
                             r"\Studio One.exe"),
                  "root": str(_USER_DOCS / "Studio One" / "Songs")},
}

def check_in_here(p, here=HERE):
    """写入前校验：规范化后必须仍落在程序目录内（防路径越界）。默认校验
    本模块 HERE；setlist 壳传测试可替换的根。"""
    p = pathlib.Path(p).resolve()
    if p.parent != here:
        raise ValueError("数据文件路径越界：%s" % p)
    return p


def load_config_at(path, corrupt):
    """读 json 配置：损坏改 .corrupt 留证隔离（防下一次保存把坏文件连同
    全部设置覆盖掉），短锁/IO 错（杀软扫描）同样降级空配置让界面能起来。
    路径参数化：两窗壳层各自传入（setlist 壳读本模块常量，测试可替换）。"""
    if pathlib.Path(path).exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except OSError:
            return {}
        except ValueError:
            try:
                os.replace(str(path), str(corrupt))
            except OSError:
                pass
            return {}
    return {}


def rotate(baks, path):
    """写前滚动备份：bak2→bak3、bak→bak2、当前→bak（误存坏数据可回退
    最近三次；路径全部来自模块级字面量常量）。当前文件不可解析（损坏且
    .corrupt 改名失败的残余）时不入备份链——不顶掉最近的好备份。"""
    for i in range(len(baks) - 1, 0, -1):
        if baks[i - 1].exists():
            os.replace(str(baks[i - 1]), str(baks[i]))
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                json.load(f)
        except (ValueError, OSError):
            return
        shutil.copy(str(path), str(baks[0]))
def load_config():
    return load_config_at(CONFIG_PATH, CONFIG_CORRUPT)


def atomic_write(path, text, here=HERE):
    """先写临时文件再原子替换，避免断电/崩溃截断数据文件；写后 fsync
    确保 os.replace 前内容已落盘（断电后替换的是完整数据而非空/半件）。
    fsync 需另开句柄：write_text 已闭柄，FlushFileBuffers（os.fsync 的
    Windows 底层）要求写权句柄，故 O_RDWR 重开仅作刷盘用。"""
    p = check_in_here(path, here=here)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    fd = os.open(os.fspath(tmp), os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(p))


def save_config_at(cfg, path, baks, corrupt, here=HERE):
    """滚动备份 + 原子写（路径参数化版，两窗壳层各自传入）。"""
    rotate(baks, path)
    atomic_write(path, json.dumps(cfg, ensure_ascii=False, indent=2),
                 here=here)


def save_config(cfg):
    save_config_at(cfg, CONFIG_PATH, CONFIG_BAKS, CONFIG_CORRUPT)


def err_str(e):
    """异常进日志的统一格式：不用 repr（会带 Python 引号转义），
    消息为空时退回异常类名，保证始终有可读信息。"""
    return str(e).strip() or type(e).__name__


def scan_library(root, ext=".cpr"):
    """工程库：<root>/<队伍>/<歌>/<歌><ext> → [{key, team, name, path}]。"""
    base = pathlib.Path(root).resolve()
    out = []
    for team in sorted(os.listdir(base), key=natural_key):
        if not team or team.startswith("."):
            continue
        tdir = base / team
        if not tdir.is_dir():
            continue
        for name in sorted(os.listdir(tdir), key=natural_key):
            if not name or name.startswith("."):
                continue
            path = tdir / name / (name + ext)
            if path.exists():
                out.append({"key": "%s/%s" % (team, name), "team": team,
                            "name": name, "path": str(path)})
    return out


