# -*- coding: utf-8 -*-
"""播完行为（endBeh，与播放列表逐位平行）：解析、playlist.json endBeh 段
存取往返（新列表/旧字典格式）、/plcmd setend 按键套用、展开/收起变换。
fake app 走真实 App 方法（unbound 调用，同 test_webapp._pl_app 模式）。"""
import types

import advance
import setlist_gui as sg


def test_parse_end_beh():
    assert advance.parse_end_beh("") == ("follow", 0.0)
    assert advance.parse_end_beh(None) == ("follow", 0.0)
    assert advance.parse_end_beh("stop") == ("stop", 0.0)
    assert advance.parse_end_beh("next") == ("next", 0.0)
    assert advance.parse_end_beh("playnext") == ("playnext", 0.0)
    assert advance.parse_end_beh("delay:30") == ("delay", 30.0)
    assert advance.parse_end_beh("delay:0") == ("delay", 0.0)
    assert advance.parse_end_beh("delay:1.5") == ("delay", 1.5)
    assert advance.parse_end_beh("delay:abc") == ("follow", 0.0)
    assert advance.parse_end_beh("delay:") == ("follow", 0.0)
    assert advance.parse_end_beh("delay:99999") == ("delay", 86400.0)
    assert advance.parse_end_beh("junk") == ("follow", 0.0)
    assert advance.valid_end_beh("") is True
    assert advance.valid_end_beh("stop") is True
    assert advance.valid_end_beh("delay:abc") is False
    assert advance.valid_end_beh("junk") is False


def _beh_fs(tmp_path, monkeypatch):
    f = tmp_path / "playlist.json"
    monkeypatch.setattr(sg, "_HERE", tmp_path)   # _atomic_write 越界校验基点
    monkeypatch.setattr(sg, "PLAYLIST_PATH", f)
    monkeypatch.setattr(sg, "_PLAYLIST_BAKS",
                        (tmp_path / "a.bak", tmp_path / "b.bak"))
    return f


def test_playlist_endbeh_roundtrip(tmp_path, monkeypatch):
    """save/load 往返（逐位列表）+ 旧字典格式兼容 + 毒化值过滤。"""
    f = _beh_fs(tmp_path, monkeypatch)
    sg.save_playlist(["A/歌一", "B/歌二"], {}, {},
                     ["delay:30", ""])
    pl = sg._load_playlist()
    assert pl["endBeh"] == ["delay:30", ""]
    # 旧字典格式（lib_key→行为，键归一宿主）随条目走
    f.write_text('{"playlist": ["A/歌一", "pt:B/歌二#1"], "durations": {}, '
                 '"durSrc": {}, "endBeh": {"A/歌一": "stop", "B/歌二": "next"}}',
                 encoding="utf-8")
    assert sg._load_playlist()["endBeh"] == ["stop", "next"]
    # 无 endBeh 键=旧文件；列表毒化值过滤
    f.write_text('{"playlist": ["A"], "durations": {}, "durSrc": {}}',
                 encoding="utf-8")
    assert sg._load_playlist()["endBeh"] == [""]
    f.write_text('{"playlist": ["A", "B"], "durations": {}, "durSrc": {}, '
                 '"endBeh": ["next", "hax"]}', encoding="utf-8")
    assert sg._load_playlist()["endBeh"] == ["next", ""]


def test_playlist_endbeh_omitted_when_empty(tmp_path, monkeypatch):
    """未使用该功能的用户：文件里不落 endBeh 键（与旧行为字节同构）。"""
    f = _beh_fs(tmp_path, monkeypatch)
    sg.save_playlist(["A/歌一"], {}, {}, [""])
    data = f.read_text(encoding="utf-8")
    assert "endBeh" not in data


def test_end_beh_expand_collapse():
    """展开/收起变换：歌槽位行为迁移到点块出口（末位），收起反向。"""
    # 展开：歌槽位行为 → 块末位（块位置=歌槽位原位）
    assert sg._end_beh_expand(["A", "B"], ["delay:5", ""], "A",
                              ["pt:A#1", "pt:A#2"]) == ["", "delay:5", ""]
    # 歌不在表 / 无点：原样
    assert sg._end_beh_expand(["A"], [""], "Z", ["pt:Z#1"]) == [""]
    assert sg._end_beh_expand(["A"], ["next"], "A", []) == ["next"]
    # 收起：块末位（出口）行为 → 歌槽位
    assert sg._end_beh_collapse(["pt:A#1", "pt:A#2", "B"],
                                ["", "next", "delay:5"], "A") \
        == ["next", "delay:5"]
    # 块在表尾
    assert sg._end_beh_collapse(["B", "pt:A#1", "pt:A#2"],
                                ["", "", "delay:5"], "A") == ["", "delay:5"]
    # 裸条目在场（expand_replace 同款守卫）：块行为丢弃
    assert sg._end_beh_collapse(["A", "pt:A#1"], ["", "next"], "A") == [""]
    # 离散多块（与 collapse_restore 镜像）：仅首块出口晋升，其余块随位丢弃
    assert sg._end_beh_collapse(["pt:A#2", "B", "pt:A#1"],
                                ["next", "", "delay:5"], "A")         == ["next", ""]
    # 行为缺位（长度漂移）容忍：出口=末位，末位缺位按 "" 处理
    assert sg._end_beh_collapse(["pt:A#1", "pt:A#2"], ["next"], "A") \
        == [""]


def _setend_app(pl=None, beh=None):
    """setend 单测宿主：resolve 以键前缀/裸键为 lib_key；「?」开头=失效条目。"""
    pl = ["A/歌一", "A/歌一", "B/歌二"] if pl is None else list(pl)
    app = types.SimpleNamespace(
        end_beh=[""] * len(pl) if beh is None else list(beh),
        pl_keys=pl, cur=None, _persist=lambda: None)

    def resolve(key):
        if key.startswith("?"):
            return None
        lib = key[3:].rpartition("#")[0] if key.startswith("pt:") else key
        return {"lib_key": lib, "song_name": key}

    app._resolve_entry = resolve
    return app


def test_song_run():
    """连续同歌块 (lo, hi)：pt/裸条目同判、失效条目自成块、离散块各归各。"""
    keys = ["pt:A#1", "pt:A#2", "B", "pt:A#3", "?", "pt:A#4"]
    resolve = _setend_app(keys)._resolve_entry
    assert [sg._song_run(keys, resolve, i) for i in range(6)] \
        == [(0, 1), (0, 1), (2, 2), (3, 3), (4, 4), (5, 5)]
    keys = ["pt:A#1", "?", "pt:A#2"]           # 失效条目截断块
    assert sg._song_run(keys, resolve, 0) == (0, 0)
    assert sg._song_run(keys, resolve, 2) == (2, 2)


def test_plcmd_setend():
    """/plcmd setend：按曲套用——行为写点击位所在连续同歌块的出口（段中位
    永无生效路径）/清除/非法值与不在列表拒绝。"""
    app = _setend_app()
    # 同键相邻多位=一个连续块：只写块出口（末位），块内其余位清空
    assert sg.App._pl_cmd_web(app, "setend", "A/歌一",
                              {"beh": "delay:15"}) is None
    assert app.end_beh == ["", "delay:15", ""]
    # 空 beh=清除回默认（播完停止）
    assert sg.App._pl_cmd_web(app, "setend", "A/歌一", {"beh": ""}) is None
    assert app.end_beh == ["", "", ""]
    # 段式歌：段中位（用户点歌的首段行）套用 → 写块出口、段中位清空
    app = _setend_app(["pt:A/歌一#1", "pt:A/歌一#2", "pt:A/歌一#3", "B/歌二"],
                      ["next", "", "", ""])
    assert sg.App._pl_cmd_web(app, "setend", "pt:A/歌一#1",
                              {"beh": "delay:15"}) is None
    assert app.end_beh == ["", "", "delay:15", ""]
    # 离散多块：套用只动点击块的出口，他块不动
    app = _setend_app(["pt:A#1", "pt:A#2", "B", "pt:A#3"], ["", "", "", ""])
    assert sg.App._pl_cmd_web(app, "setend", "pt:A#1", {"beh": "next"}) is None
    assert app.end_beh == ["", "next", "", ""]
    # 末块的段中位套用推进行为：出口=末条目，驳回（仅可播完停止）
    app = _setend_app(["B", "pt:A#1", "pt:A#2"])
    assert sg.App._pl_cmd_web(app, "setend", "pt:A#1",
                              {"beh": "next"}) == "已是播放列表末尾，仅可播完停止"
    assert app.end_beh == ["", "", ""]          # 驳回不落盘
    assert sg.App._pl_cmd_web(app, "setend", "pt:A#1", {"beh": ""}) is None
    # 不在播放列表 / 非法值 / 非末条目不受限
    app = _setend_app(["A/歌一", "B/歌二"])
    assert sg.App._pl_cmd_web(app, "setend", "A/歌一",
                              {"beh": "next"}) is None
    assert app.end_beh == ["next", ""]
    assert sg.App._pl_cmd_web(app, "setend", "X/无",
                              {"beh": "stop"}) == "条目不在播放列表里"
    assert sg.App._pl_cmd_web(app, "setend", "A/歌一",
                              {"beh": "hax"}) == "未知播完行为：hax"


def _plcmd_app(pl=None, beh=None):
    """结构操作单测宿主：A=已启用定位点的歌（3 点）、B/X=裸歌；「?」失效。"""
    pl = ["pt:A#1", "pt:A#2", "B"] if pl is None else list(pl)
    app = types.SimpleNamespace(
        pl_keys=list(pl),
        end_beh=[""] * len(pl) if beh is None else list(beh),
        cur=None, _persist=lambda: None,
        by_key={"A": {"key": "A", "name": "A", "path": "/a", "team": "T"},
                "B": {"key": "B", "name": "B", "path": "/b", "team": "T"},
                "X": {"key": "X", "name": "X", "path": "/x", "team": "T"}},
        points={"A": {"enabled": True, "counter": 3, "points": [
            {"id": 1, "name": "p1", "start": 0.0, "end": 10.0},
            {"id": 2, "name": "p2", "start": 10.0, "end": 20.0},
            {"id": 3, "name": "p3", "start": 20.0, "end": 30.0}]},
            "X": {"enabled": False, "counter": 0, "points": []}})

    def resolve(key):
        if key.startswith("?"):
            return None
        if key.startswith("pt:"):
            host, _, pid = key[3:].rpartition("#")
            p = next((x for x in app.points.get(host, {}).get("points", [])
                      if x["id"] == int(pid)), None)
            if p is None or host not in app.by_key:
                return None
            return {"lib_key": host, "song_name": host, "point": p,
                    "display": "%s · %s" % (host, p["name"]),
                    "path": app.by_key[host]["path"]}
        if key not in app.by_key:
            return None
        return {"lib_key": key, "song_name": key, "point": None,
                "display": key, "path": app.by_key[key]["path"]}

    app._resolve_entry = resolve
    return app


def test_end_beh_renormalize():
    """归一：出口值优先保留、否则块内最后一个非空值晋升、段中位清空、
    列表末位出口不带推进行为（与 setend 末位闸同口径）、长度对齐。"""
    app = _plcmd_app(["pt:A#1", "pt:A#2", "pt:A#3", "B"])
    b = ["stop", "", "", "next"]           # 旧版死值在段中位+末位推进值
    sg._end_beh_renormalize(app.pl_keys, app._resolve_entry, b)
    assert b == ["", "", "stop", ""]
    b = ["", "playnext", "", ""]
    sg._end_beh_renormalize(app.pl_keys, app._resolve_entry, b)
    assert b == ["", "", "playnext", ""]
    b = ["", "", "next", ""]               # 出口值已在对的位置：幂等
    sg._end_beh_renormalize(app.pl_keys, app._resolve_entry, b)
    assert b == ["", "", "next", ""]
    b = ["delay:5"]                        # 长度漂移：补齐后块重算
    sg._end_beh_renormalize(app.pl_keys, app._resolve_entry, b)
    assert b == ["", "", "delay:5", ""]


def test_plcmd_struct_moves_exit_beh():
    """结构编辑（add/remove/move）迁移块出口行为——出口由列表结构派生，
    位移后行为随块走，不静默丢、不沦为死槽位。"""
    # remove 删出口段：行为迁到幸存出口
    app = _plcmd_app(["pt:A#1", "pt:A#2", "B"], ["", "delay:15", ""])
    assert sg.App._pl_cmd_web(app, "remove", "pt:A#2") is None
    assert app.pl_keys == ["pt:A#1", "B"]
    assert app.end_beh == ["delay:15", ""]
    # remove 删段中段：出口行为不动
    app = _plcmd_app(["pt:A#1", "pt:A#2", "B"], ["", "next", ""])
    assert sg.App._pl_cmd_web(app, "remove", "pt:A#1") is None
    assert app.pl_keys == ["pt:A#2", "B"]
    assert app.end_beh == ["next", ""]
    # add 只在表尾追加：并入尾块时旧出口值随块迁到新出口——但新出口已是
    # 末位，推进行为按末位闸清空（旧值本就越权：追加前它就在末位上）
    app = _plcmd_app(["B", "pt:A#1", "pt:A#2"], ["", "", "playnext"])
    assert sg.App._pl_cmd_web(app, "add", "pt:A#3") is None
    assert app.pl_keys == ["B", "pt:A#1", "pt:A#2", "pt:A#3"]
    assert app.end_beh == ["", "", "", ""]
    # add 追加到表尾而他曲之后：自成新块，原块出口行为不动
    app = _plcmd_app(["pt:A#1", "pt:A#2", "B"], ["", "next", ""])
    assert sg.App._pl_cmd_web(app, "add", "pt:A#3") is None
    assert app.pl_keys == ["pt:A#1", "pt:A#2", "B", "pt:A#3"]
    assert app.end_beh == ["", "next", "", ""]
    # move 把出口段拖去列表末尾：源块幸存出口接管；落到末位的随行值按
    # 末位闸清空（无下一首可推进）
    app = _plcmd_app(["pt:A#1", "pt:A#2", "B"], ["", "next", ""])
    assert sg.App._pl_cmd_web(app, "move", "pt:A#2",
                              {"from": 1, "to": 2, "key": "pt:A#2"}) is None
    assert app.pl_keys == ["pt:A#1", "B", "pt:A#2"]
    assert app.end_beh == ["next", "", ""]
    # move 他曲插入块中：拆块后各块出口就位（首块无值=播完停止是编排事实）
    app = _plcmd_app(["pt:A#1", "pt:A#2", "B"], ["", "next", ""])
    assert sg.App._pl_cmd_web(app, "move", "B",
                              {"from": 2, "to": 1, "key": "B"}) is None
    assert app.pl_keys == ["pt:A#1", "B", "pt:A#2"]
    assert app.end_beh == ["", "", ""]


def test_plcmd_add_rejects_bare_of_enabled():
    """已启用定位点的歌加裸条目=「裸+点块」混合态工厂（再展开造重复
    pt 行键）：拒绝，加入走展开段条目。"""
    app = _plcmd_app(["B"])
    assert sg.App._pl_cmd_web(app, "add", "X") is None      # 未启用：可加
    assert sg.App._pl_cmd_web(app, "add", "A") == \
        "该歌已启用定位点：请从展开的段条目加入"
    assert "A" not in app.pl_keys


def _advance_app(lib_of, pl, cur, end_beh=None):
    """_advance 单测宿主：lib_of={key: lib_key} 映射；end_beh=逐位列表。
    _switch/_transport/定时器全部捕获式 stub。"""
    app = types.SimpleNamespace(
        ctrl=types.SimpleNamespace(busy=False),
        pl_keys=list(pl), cur=cur,
        end_beh=list(end_beh) if end_beh is not None else [""] * len(pl),
        _delay_seq=0,
        switches=[], delayed=[], logs=_Log(),
        _resolve_entry=lambda k: ({"lib_key": lib_of[k],
                                   "song_name": k}
                                  if k in lib_of else None),
        _switch=lambda i, via, play_after=False:
            app.switches.append((i, play_after)) or True,
        _transport=lambda a: app.transports.append(a),
        _transport_state=lambda: getattr(app, "ts", "stopped"),
        _auto_play=lambda: app.plays.append(1),
        _schedule_delay_play=lambda i, d: app.delayed.append((i, d)),
        _delay_play_rearm=lambda seq, key, tries, ptries:
            app.rearms.append((seq, key, tries, ptries)))
    app.transports = []
    app.rearms = []
    app.plays = []
    app.q = app.logs
    return app


class _Log(list):
    def put(self, msg):
        self.append(msg)


def test_advance_song_end_semantics():
    """歌尾行为读当前条目槽位：段间链进（点→点）不解析行为，无条件接续
    播放；末点（出口）槽位的行为在跨出该歌时解析（未定义=停止不切）。"""
    app = _advance_app({"pt:A#1": "A", "pt:A#2": "A", "B": "B"},
                       ["pt:A#1", "pt:A#2", "B"], cur=0,
                       end_beh=["", "stop", ""])
    sg.App._advance(app)
    assert app.switches == [(1, True)]          # 段间：无条件接续播放
    app.cur = 1
    app.switches = []                           # 清第一段记录：只看本次推进
    sg.App._advance(app)
    assert app.switches == []                   # 歌尾：出口槽位 stop → 不切
    assert any("播完停止" in m for m in app.logs)
    # 未定义行为=播完停止：不切
    app2 = _advance_app({"A": "A", "B": "B"}, ["A", "B"], cur=0)
    sg.App._advance(app2)
    assert app2.switches == [] and any("播完停止" in m for m in app2.logs)


def test_advance_delay_and_stop_paths():
    """delay=切歌（不播）+ 排定延迟起播；末尾=不排定。"""
    app = _advance_app({"A": "A", "B": "B"}, ["A", "B"], cur=0,
                       end_beh=["delay:15", ""])
    sg.App._advance(app)
    assert app.switches == [(1, False)]         # 切过去不播
    assert app.delayed == [(1, 15.0)]
    # 已是末尾：无下一首可排（_switch 越界拒绝路径）
    end_app = _advance_app({"A": "A"}, ["A"], cur=0,
                           end_beh=["delay:15"])
    end_app._switch = lambda i, via, play_after=False: False
    sg.App._advance(end_app)
    assert end_app.switches == [] and end_app.delayed == []


def test_advance_explicit_next_playnext():
    """显式行为三分支：next=切不起播、playnext=切并起播、stop=不切。"""
    for beh, want_play in (("next", False), ("playnext", True)):
        app = _advance_app({"A": "A", "B": "B"}, ["A", "B"], cur=0,
                           end_beh=[beh, ""])
        sg.App._advance(app)
        assert app.switches == [(1, want_play)], beh
    app = _advance_app({"A": "A", "B": "B"}, ["A", "B"], cur=0,
                       end_beh=["stop", ""])
    sg.App._advance(app)
    assert app.switches == [] and any("播完停止" in m for m in app.logs)


def test_delay_play_fire_gates():
    """到时闸：代际失效/指针他移/装载中重探/用户已播不抢/尾流重探/停止起播。"""
    app = _advance_app({}, ["k"], cur=0)
    app._delay_seq = 7
    sg.App._delay_play_fire(app, 6, "k")        # 代际失效
    assert app.transports == []
    sg.App._delay_play_fire(app, 7, "other")    # 指针他移
    assert app.transports == []
    app.ts = "playing"
    sg.App._delay_play_fire(app, 7, "k")
    assert app.transports == []                 # 用户已起播不抢
    app.ts = "paused"
    sg.App._delay_play_fire(app, 7, "k")
    assert app.rearms == [(7, "k", 0, 1)]       # 尾流：1 秒重探（独立计数）
    app.ts = "stopped"
    sg.App._delay_play_fire(app, 7, "k")
    assert app.plays == [1]                     # 纪元闭合：交 _auto_play
    app.ts = "stopped"
    app.ctrl = types.SimpleNamespace(busy=True)
    sg.App._delay_play_fire(app, 7, "k")
    assert app.rearms[-1] == (7, "k", 1, 0)     # 装载中：重探


def test_entry_alive_bare_of_enabled():
    """存活判定：已启用定位点的歌的裸条目=混合态（装载即失效，与 add
    拒入同口径）；未启用裸条目/库缺/点缺各自判死。"""
    app = types.SimpleNamespace(
        by_key={"A": {"key": "A"}, "B": {"key": "B"}},
        points={"A": {"enabled": True, "counter": 1, "points": [
                     {"id": 1, "name": "p1", "start": 0.0, "end": 9.0}]},
                "B": {"enabled": False, "counter": 0, "points": []}})
    assert sg.App._entry_alive(app, "A") is False       # 已启用：裸条目=混合态
    assert sg.App._entry_alive(app, "B") is True
    assert sg.App._entry_alive(app, "C") is False       # 库缺
    assert sg.App._entry_alive(app, "pt:A#1") is True
    assert sg.App._entry_alive(app, "pt:A#9") is False  # 点缺
    assert sg.App._entry_alive(app, "pt:B#1") is False  # 未启用
