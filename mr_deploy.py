# -*- coding: utf-8 -*-
r"""Cubase MIDI Remote 直控部署助手（README 旧手工三步「铺脚本/立口/重启
确认」的程序化等价物）。

- 铺脚本：mr_driver JS -> 文档目录 Steinberg\Cubase\MIDI Remote\Driver
  Scripts\Local\...（Cubase 只在启动时扫描该目录，无热加载 -> 部署后需
  重启 Cubase）；
- 立口：loopMIDI 端口=注册表值名（HKCU\...\loopMIDI\Ports，值名=端口名、
  DWORD 数据=启用标志，11-15 本机实证），写值后重启 loopMIDI 生效；
- 确认绑定：外部不可读（回声方案实证作废，mr_ctrl docstring 有案），状态
  卡以「进程在+口在+脚本在」三查当代理判据，真效验由走带失败报错兜底。
"""
import hashlib
import os
import pathlib
import re
import shutil
import sys
import time
import threading
import winreg
import ctypes

import midi_bridge as mbridge
from obs_ctrl import close_loopmidi, find_processes_by_prefix, launch_detached

PORT_NAME = "CubeSetlist Remote"
_REG_PORTS = r"Software\Tobias Erichsen\loopMIDI\Ports"
_LOCAL = r"Local\CubeSetlistManager\CubeSetlistRemote"
# S1 bundle 源/目标共用相对结构（无 Cubase 的 Local 级）
_S1_LOCAL = "CubeSetlistManager/CubeSetlistRemote"

_op_lock = threading.Lock()   # 部署/立口互斥（双 POST 并发防双杀 loopMIDI）


def _documents_dir() -> str:
    r"""Known Folder「文档」：用户重定向过文档目录时 %USERPROFILE%\Documents
    是假地址，Cubase 实际扫描 Known Folder 下的 Steinberg 树。"""
    buf = ctypes.c_wchar_p()
    ref = (ctypes.c_char * 16).from_buffer_copy(
        b"\xd0\x9a\xd3\xfd\x8f\x23\xaf\x46\xad\xb4\x6c\x85\x48\x03\x69\xc7")
    r = ctypes.windll.shell32.SHGetKnownFolderPath(
        ctypes.byref(ref), 0, None, ctypes.byref(buf))
    ctypes.windll.ole32.CoTaskMemFree(buf)
    if r == 0 and buf.value:
        return buf.value
    return os.path.join(os.environ.get("USERPROFILE", ""), "Documents")


def driver_src() -> pathlib.Path:
    """随包 driver 目录：源码态=仓库 mr_driver，打包态=_internal/mr_driver
    （与 webapp 资产同口径）。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", "") or \
            pathlib.Path(sys.executable).resolve().parent
    else:
        base = pathlib.Path(__file__).resolve().parent
    return pathlib.Path(base) / "mr_driver" / _LOCAL


def driver_dst() -> pathlib.Path:
    return pathlib.Path(_documents_dir()) / "Steinberg" / "Cubase" / \
        "MIDI Remote" / "Driver Scripts" / _LOCAL


def _deployed_js():
    dst = driver_dst()
    try:
        return [(p.name, p.read_bytes()) for p in dst.glob("*.js")]
    except OSError:
        return []


def deploy_driver() -> str:
    """铺 driver 脚本（幂等：hash 相同不重写）；返回空串=成功。"""
    with _op_lock:
        src = driver_src()
        if not src.is_dir():
            return "随包 driver 目录缺失：%s" % src
        try:
            dst = driver_dst()
            dst.mkdir(parents=True, exist_ok=True)
            for f in src.glob("*.js"):
                target = dst / f.name
                if not target.exists() or \
                        hashlib.sha1(f.read_bytes()).digest() != \
                        hashlib.sha1(target.read_bytes()).digest():
                    shutil.copyfile(f, target)
            return ""
        except OSError as e:
            return "部署失败：%s" % e


def s1_driver_src() -> pathlib.Path:
    """随包 S1 控制器脚本 bundle：源码态=仓库 s1_driver，打包态=_internal/
    s1_driver（与 mr_driver/webapp 同口径）。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", "") or \
            pathlib.Path(sys.executable).resolve().parent
    else:
        base = pathlib.Path(__file__).resolve().parent
    return pathlib.Path(base) / "s1_driver" / _S1_LOCAL


def s1_dst() -> pathlib.Path:
    r"""S1 用户 Devices 文件夹：%APPDATA%\PreSonus\Studio One <N>\Devices\
    （Settings 目录内的 Devices 子夹；S1 启动时扫描，需重启加载）。取
    「含 Studio One.settings 的最高版本目录」；从未运行过 S1 的机器退回
    最高已知版本默认名（首个装机也能预铺）。"""
    base = pathlib.Path(os.environ.get("APPDATA", "")) / "PreSonus"
    best = None
    try:
        dirs = os.listdir(base)
    except OSError:
        dirs = []
    for d in dirs:
        if not d.lower().startswith("studio one"):
            continue
        if not (base / d / "Studio One.settings").exists():
            continue
        m = re.search(r"(\d+)", d)
        v = int(m.group(1)) if m else 0
        if best is None or v > best[0]:
            best = (v, base / d / "Devices")
    if best is None:
        best = (7, base / "Studio One 7" / "Devices")
    return best[1] / _S1_LOCAL


def deploy_s1_driver() -> str:
    """铺 S1 控制器脚本（幂等：hash 相同不重写）；返回空串=成功。
    bundle 含 .device/.surface.xml/.js 三件，全拷（部署后需重启 S1）。"""
    with _op_lock:
        src = s1_driver_src()
        if not src.is_dir():
            return "随包 S1 脚本目录缺失：%s" % src
        try:
            dst = s1_dst()
            dst.mkdir(parents=True, exist_ok=True)
            for f in src.iterdir():
                if not f.is_file():
                    continue
                target = dst / f.name
                if not target.exists() or \
                        hashlib.sha1(f.read_bytes()).digest() != \
                        hashlib.sha1(target.read_bytes()).digest():
                    shutil.copyfile(f, target)
            return ""
        except OSError as e:
            return "部署失败：%s" % e


def port_ok() -> bool:
    """「CubeSetlist Remote」口在 winmm 枚举（pair 口，OUT 清单在即成立）。"""
    return any(n == PORT_NAME for _, n in mbridge.out_devices())


def loopmidi_exe() -> str:
    """loopMIDI 可执行路径（复用 midi_bridge 桥内既有探测：Run 键优先，
    退回约定安装位；找不到返回空串）。"""
    return mbridge.loopmidi_exe() or ""


def create_port() -> str:
    """立口：注册表写值 -> 重启 loopMIDI -> 等 winmm 枚举出现。空串=成功。"""
    with _op_lock:
        if port_ok():
            return ""
        try:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_PORTS)
            with key:
                winreg.SetValueEx(key, PORT_NAME, 0, winreg.REG_DWORD, 1)
        except OSError as e:
            return "注册表写入失败：%s" % e
        was_up = bool(find_processes_by_prefix("loopmidi"))
        exe = loopmidi_exe()
        if not exe:
            return "未找到 loopMIDI 程序：请先安装 loopMIDI" \
                "（tobias-erichsen.de/software/loopmidi.html）"
        if was_up:
            close_loopmidi()
        launch_detached(exe)
        for _ in range(8):                # ~4s：loopMIDI 起稳即枚举出
            if port_ok():
                return ""
            time.sleep(0.5)
        return "端口已写入注册表，但 loopMIDI 重启后未在枚举中出现" \
            "（loopMIDI 版本不认？请手动在 loopMIDI 界面新建「%s」）" \
            % PORT_NAME


def status(proc_prefix: str = "cubase") -> dict:
    """部署状态快照（设置卡+主控台引导条数据源）。"""
    src = driver_src()
    js = _deployed_js()
    src_js = {p.name: p.read_bytes() for p in src.glob("*.js")} \
        if src.is_dir() else {}
    cur = bool(src_js) and {n for n, _ in js} == set(src_js) and \
        all(hashlib.sha1(b).digest() ==
            hashlib.sha1(src_js[n]).digest() for n, b in js)
    return {
        "src": bool(src_js),
        "script": cur,
        "stale": bool(js) and not cur,
        "port": port_ok(),
        "loopmidi": bool(find_processes_by_prefix("loopmidi"))
        or bool(loopmidi_exe()),
        "daw": bool(find_processes_by_prefix(proc_prefix)),
    }


def s1_status() -> dict:
    """S1 侧部署状态快照（端口与 Cubase 共用；字段名与 status 对齐，
    脚本三字段出自 s1 bundle 路径）。"""
    src = s1_driver_src()
    dst = s1_dst()
    src_files = {p.name: p.read_bytes() for p in src.iterdir()} \
        if src.is_dir() else {}
    dst_files = {}
    try:
        dst_files = {p.name: p.read_bytes() for p in dst.iterdir()
                     if p.is_file()}
    except OSError:
        pass
    cur = bool(src_files) and set(dst_files) == set(src_files) and \
        all(hashlib.sha1(b).digest() ==
            hashlib.sha1(src_files[n]).digest() for n, b in dst_files.items())
    return {
        "src": bool(src_files),
        "script": cur,
        "stale": bool(dst_files) and not cur,
        "port": port_ok(),
        "loopmidi": bool(find_processes_by_prefix("loopmidi"))
        or bool(loopmidi_exe()),
        "daw": bool(find_processes_by_prefix("studio one")),
    }
