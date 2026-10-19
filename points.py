# -*- coding: utf-8 -*-
"""定位点（工程内段式播放条目）纯逻辑：sidecar 读写 / 校验 / MMC GOTO 字节 /
播放列表替换变换。不 import Tk、不碰 winmm——全部可零设备测试。

条目模型：每个播放列表条目定义一个播放区间 [起点, 终点]（工程绝对时码秒）。
定位点条目的 key 合成为 "pt:<素材库key>#<id>"，定义本体存工程目录下的
setlist_points.json（enabled/counter/points）——勾选开关只翻 enabled，定义
不随停用消失；播放列表删点条目=仅移出节目单，删定义只在设置窗。

推进判定与裸工程同一台机器（AdvanceWatch）：装载 set_duration(终点-起点,
left=起点) 后，"已播=位置-起点、到终点停、99% 资格闩"逐条原样成立。
MMC GOTO = F0 7F <dev> 06 44 06 01 <时><分><秒><帧><子帧> F7（SMPTE 绝对
时间，帧率码进小时字节高 2 位），30fps 与 MTC 时码管线同一口径。"""
import json
import os
import pathlib

SIDECAR = "setlist_points.json"
SEP = " · "                     # 播放列表显示名连接符（工程名 · 点名）
NAME_FMT = "定位点%d"
MIN_SPAN = 1.0                  # 硬校验：终点必须大于起点
SOFT_SPAN = 3.0                 # 软提示：段过短吃停止重试窗，只提示不拦
MHDR_DONE = 0x1


def empty_data():
    return {"enabled": False, "counter": 0, "points": []}


def initial_data(end):
    """启用时的初始点：定位点1 = [0:00, 工程末尾]（end 未知传 0，设置窗
    里补填后才可入列）。"""
    return {"enabled": True, "counter": 1,
            "points": [{"id": 1, "name": NAME_FMT % 1, "start": 0.0,
                        "end": float(end or 0.0)}]}


def _has_dotdot(path):
    """路径里是否含 ".." 分量（统一切分隔符，防穿越的字面校验）。"""
    return ".." in path.replace("\\", "/").split("/")


def load_points(song_dir):
    """读 sidecar；缺失/损坏按停用空表处理（损坏在 err 里带话，调用方告警）。
    路径规范化+包含校验内联在此（song_dir 来自可配置的工程库根，防穿越）。"""
    if _has_dotdot(song_dir):
        return empty_data(), "定位点目录非法：%s" % song_dir
    base = os.path.normpath(os.path.abspath(song_dir))
    path = os.path.normpath(os.path.abspath(os.path.join(base, SIDECAR)))
    if _has_dotdot(path) or not path.startswith(base + os.sep):
        return empty_data(), "定位点目录非法：%s" % song_dir
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return empty_data(), None
    except NotADirectoryError as e:
        return empty_data(), "定位点目录异常（%s）：按未启用处理" % e
    except (OSError, ValueError) as e:
        return empty_data(), "定位点文件读取失败（%s）：按未启用处理" % e
    if not isinstance(data, dict):
        return empty_data(), "定位点文件格式异常：按未启用处理"
    out = empty_data()
    out["enabled"] = bool(data.get("enabled"))
    try:
        out["counter"] = int(data.get("counter") or 0)
    except (TypeError, ValueError):
        out["counter"] = 0
    pts = []
    for p in data.get("points") or []:
        try:
            pts.append({"id": int(p["id"]), "name": str(p["name"]),
                        "start": float(p["start"]), "end": float(p["end"])})
        except (KeyError, TypeError, ValueError):
            continue            # 单条脏数据跳过，不拖垮整表
    out["points"] = pts
    return out, None


def save_points(song_dir, data):
    """sidecar 原子落盘。路径规范化+包含校验内联在此（同 load_points）。
    目录不代建——工程目录由素材库扫描保证在盘，不在盘即 OSError 上抛。"""
    if _has_dotdot(song_dir):
        raise ValueError("定位点目录非法：%s" % song_dir)
    base = pathlib.Path(song_dir).resolve()
    path = base / SIDECAR
    if _has_dotdot(str(path)) or path.parent != base:
        raise ValueError("定位点目录非法：%s" % song_dir)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(path)


def get_point(data, pid):
    for p in data["points"]:
        if p["id"] == pid:
            return p
    return None


def validate_point(start, end):
    """(拒绝原因, 软提示)：拒绝原因非 None 不得保存/入列。"""
    if end <= start:
        return ("结束点必须大于开始点", None)
    if end - start < MIN_SPAN:
        return ("段长不足 1 秒", None)
    if end - start < SOFT_SPAN:
        return (None, "段长不足 3 秒：停止重试窗可能吃掉自动推进")
    return (None, None)


def pt_key(lib_key, pid):
    return "pt:%s#%d" % (lib_key, pid)


def parse_pt_key(key):
    """"pt:<素材库key>#<id>" → (素材库key, id) | None。素材库 key 本身含
    "/"（队伍/歌），按前缀与末段 # 解析，与素材库 key 空间零冲突。"""
    if not key.startswith("pt:"):
        return None
    head, sep, tail = key[3:].rpartition("#")
    if not sep or not tail.isdigit():
        return None
    return head, int(tail)


def pt_keys_for(lib_key, data):
    return [pt_key(lib_key, p["id"]) for p in data["points"]]


def display_name(lib_name, point_name):
    return lib_name + SEP + point_name


def next_point(data, start, end):
    """新点：默认名按 counter 递增（删除不回补），返回 (点, 自增后的data)。"""
    data["counter"] = int(data.get("counter", 0)) + 1
    pid = data["counter"]
    pt = {"id": pid, "name": NAME_FMT % pid,
          "start": float(start or 0.0), "end": float(end or 0.0)}
    data["points"] = list(data.get("points") or []) + [pt]
    return pt, data


def expand_replace(pl_keys, lib_key, pt_keys):
    """启用替换：裸条目原位替换为点条目序列。裸条目不在列表则原样返回
    （此时由用户从素材库手动加）。"""
    if lib_key not in pl_keys or not pt_keys:
        return list(pl_keys)
    out = []
    for k in pl_keys:
        if k == lib_key:
            out.extend(pt_keys)
        else:
            out.append(k)
    return out


def collapse_restore(pl_keys, lib_key):
    """停用还原：移除该工程全部点条目，裸条目放回首个点条目的位置。
    点条目不在列表（或裸条目还在）则原样返回。"""
    prefix = "pt:%s#" % lib_key
    if lib_key in pl_keys:
        return [k for k in pl_keys if not k.startswith(prefix)]
    out = []
    restored = False
    for k in pl_keys:
        if k.startswith(prefix) and not restored:
            out.append(lib_key)
            restored = True
        elif not k.startswith(prefix):
            out.append(k)
    return out


def remap_cur(old_keys, new_keys, cur, lib_key):
    """列表变换后的当前条目映射：原条目还在就跟随；原条目被替换/移除则
    落到该工程的首个点条目（启用）或还原的裸条目（停用）；都不在则 None。"""
    if cur is None or not (0 <= cur < len(old_keys)):
        return None
    old = old_keys[cur]
    if old in new_keys:
        return new_keys.index(old)
    prefix = "pt:%s#" % lib_key
    for i, k in enumerate(new_keys):
        if k.startswith(prefix):
            return i
    if lib_key in new_keys:
        return new_keys.index(lib_key)
    return None


def mmc_goto_bytes(sec, fps=30.0, dev=127):
    """秒 → MMC GOTO SysEx（F0 7F <dev> 06 44 06 01 时 分 秒 帧 子帧 F7）。
    帧率码：0=24 1=25 2=30df 3=30——与 mtc._FPS 同口径；子帧恒 0。"""
    fps_code = {24.0: 0, 25.0: 1, 30.0: 3}.get(float(fps), 3)
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int(sec // 60) % 60
    s = int(sec) % 60
    f = int(round((sec - int(sec)) * fps))
    if f >= fps:
        f = int(fps) - 1
    return bytes([0xF0, 0x7F, dev & 0x7F, 0x06, 0x44, 0x06, 0x01,
                  (fps_code << 5) | (h & 0x1F), m, s, f, 0x00, 0xF7])


def fmt_tc(sec):
    """绝对时码显示：满 1 小时带时位（h:mm:ss），否则 mm:ss。"""
    sec = max(0, int(round(float(sec))))
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    if h:
        return "%d:%02d:%02d" % (h, m, s)
    return "%d:%02d" % (m, s)
