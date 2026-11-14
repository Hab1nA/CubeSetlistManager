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


def _setend_app():
    return types.SimpleNamespace(
        end_beh=["", ""], pl_keys=["A/歌一", "A/歌一"], cur=None,
        _persist=lambda: None)


def test_plcmd_setend():
    """/plcmd setend：按键套用全部同键条目位/清除/非法值与不在列表拒绝。"""
    app = _setend_app()
    # 同键多位（导入文件可含）：按键套用到全部位
    assert sg.App._pl_cmd_web(app, "setend", "A/歌一",
                              {"beh": "delay:15"}) is None
    assert app.end_beh == ["delay:15", "delay:15"]
    # 空 beh=清除回默认（播完停止）
    assert sg.App._pl_cmd_web(app, "setend", "A/歌一", {"beh": ""}) is None
    assert app.end_beh == ["", ""]
    # pt 条目键=独立条目位
    app.pl_keys = ["pt:A/歌一#3", "A/歌一"]
    app.end_beh = ["", "next"]
    assert sg.App._pl_cmd_web(app, "setend", "pt:A/歌一#3",
                              {"beh": "delay:15"}) is None
    assert app.end_beh == ["delay:15", "next"]
    # 不在播放列表 / 非法值
    assert sg.App._pl_cmd_web(app, "setend", "X/无",
                              {"beh": "stop"}) == "条目不在播放列表里"
    assert sg.App._pl_cmd_web(app, "setend", "A/歌一",
                              {"beh": "hax"}) == "未知播完行为：hax"


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
