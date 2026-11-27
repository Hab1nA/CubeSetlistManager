# -*- coding: utf-8 -*-
"""谱面服务器（scoreServer）启动信号：DAW 时间线上的启动音符 → 通知谱面
服务器（目前是鼓手电脑上的 drum-transcriber 房间）「数 N 小节预备拍后从
这首歌的起点进」。

信号链：DAW 在起点前 N 小节画一个启动音符（默认 D2=50）→ 翻谱信号
loopMIDI 口（webRemote.midiIn，与翻谱同口）→ 本模块 →
POST <url>/api/room/cue?bars=N&song=<歌名>（头 X-Room-Token）。

谱面服务器从「收到请求那一刻」开始按它那首歌的速度数预备拍，所以：
- 启动音符不进翻谱的 100ms 组合窗口，到达即发（窗口=整体晚 100ms）；
- 每次新建连接，不复用、不重试：复用旧连接失败后重发可能让对端收到两次
  cue（第二次从更晚的时刻重数），局域网新建 TCP 只要几毫秒；
- 切歌完成时先发 /api/room/song 让对端提前加载音频（preload），cue 时不用等。

接口是谱面服务器自己的（drum-transcriber 的 docs/band-room.md），本模块只做调用方。
"""
import http.client
import queue
import threading
import time
import urllib.parse

DEFAULT_SCORE_SERVER = {
    "enabled": False,
    "url": "",          # 谱面服务器地址，如 http://192.168.1.20:8765
    "token": "",        # 谱面服务器的房间 token（drum-transcriber「房间」面板里）
    "note": 50,         # 启动音符：D2（翻谱 48/49 与设备 72–81 之外，VJ 区也不用）
    "bars": 2,          # 预备拍小节数：DAW 里启动音符画在起点前几小节就填几
}
TIMEOUT = 2.0
# 翻谱口上的其它消费者：48/49=翻页命令、72–81=设备槽位。启动音符撞号时
# _on_midi 会把它先截给本模块，翻谱动作静默丢失——启用态直接拒绝。
TURN_NOTES = frozenset((48, 49) + tuple(range(72, 82)))


def parse_url(url):
    """'http://host:port' → (host, port, base_path)；不合法抛 ValueError。"""
    u = urllib.parse.urlsplit(str(url or "").strip())
    if u.scheme != "http" or not u.hostname:
        raise ValueError("地址须是 http://<IP>:<端口>")
    return u.hostname, u.port or 80, u.path.rstrip("/")


def cfg_int(v, default):
    """手改配置容错：非整数（None/空/乱填）回落默认值，不炸启动。"""
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def build_path(base, action, song=None, bars=None):
    q = {}
    if bars is not None:
        q["bars"] = int(bars)
    if song:
        q["song"] = song
    path = "%s/api/room/%s" % (base, action)
    return path + ("?" + urllib.parse.urlencode(q) if q else "")


class ScoreServer:
    """App 持有一个实例。submit() 可在 winmm 回调线程调用（只入队）；
    请求由 daemon worker 串行发出，结果走 report（主窗口日志）。"""

    def __init__(self, cfg, report, clock=time.monotonic, post=None):
        self._report = report
        self._clock = clock
        self._post = post or self._http_post
        self.song = None            # 当前歌名（切歌完成时 preload 记下）
        self.last = None            # 最近一次信号结果（设置页状态行）
        self.apply(cfg)
        self.q = queue.Queue()
        threading.Thread(target=self._loop, daemon=True).start()

    def apply(self, cfg):
        c = dict(DEFAULT_SCORE_SERVER)
        c.update(cfg if isinstance(cfg, dict) else {})
        self.enabled = bool(c.get("enabled"))
        self.url = str(c.get("url") or "")
        self.token = str(c.get("token") or "")
        self.note = cfg_int(c.get("note"), 50)
        self.bars = max(0, min(8, cfg_int(c.get("bars"), 2)))
        self._target = None
        if self.enabled:
            if self.note in TURN_NOTES:
                self.enabled = False
                self._report("谱面服务器未启用：启动音符 %d 与翻谱音符冲突"
                             % self.note)
                return
            try:
                self._target = parse_url(self.url)
            except ValueError as e:
                self._report("谱面服务器未启用：%s，当前 %r" % (e, self.url))

    @property
    def active(self):
        return self._target is not None

    # -- 入口 --

    def on_note(self, note):
        """winmm 回调线程：命中启动音符才入队，返回是否已消费。"""
        if self.active and note == self.note:
            self.q.put(("cue", self.song, self._clock()))
            return True
        return False

    def preload(self, song):
        """切歌完成（Tk 主线程）：记下歌名并让对端提前加载。"""
        self.song = song or None
        if self.active and self.song:
            self.q.put(("song", self.song, self._clock()))

    def close(self):
        self.q.put(None)

    # -- worker --

    def _loop(self):
        while True:
            job = self.q.get()
            if job is None:
                return
            try:
                self._send(*job)
            except Exception as e:      # worker 绝不静默死亡
                try:
                    self._report("谱面服务器信号异常，已恢复：%s: %s"
                                 % (type(e).__name__, e))
                except Exception:
                    pass

    def _send(self, action, song, t0):
        target = self._target
        if target is None:
            return
        host, port, base = target
        bars = self.bars if action == "cue" else None
        path = build_path(base, action, song, bars)
        status, body, err = self._post(host, port, path, self.token)
        lag = (self._clock() - t0) * 1000
        what = ("启动《%s》，预备 %d 小节" % (song, bars) if action == "cue" and song
                else "启动当前歌，预备 %d 小节" % bars if action == "cue"
                else "预载《%s》" % song)
        if err:
            msg = "谱面服务器%s失败：%s" % (what, err)
        elif status >= 300:
            msg = "谱面服务器%s被拒 %d：%s" % (what, status, body[:120])
        elif action == "cue":
            msg = "谱面服务器%s已送达，%.0f ms" % (what, lag)
        else:
            msg = "谱面服务器%s完成" % what
        self.last = {"ok": not err and status < 300, "msg": msg}
        self._report(msg)

    @staticmethod
    def _http_post(host, port, path, token):
        """返回 (status, body, err)。http.client 直连不走系统代理。"""
        conn = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
        try:
            headers = {"Content-Length": "0"}
            if token:
                headers["X-Room-Token"] = token
            conn.request("POST", path, headers=headers)
            r = conn.getresponse()
            return r.status, r.read().decode("utf-8", "replace"), None
        except Exception as e:
            return 0, "", "%s: %s" % (type(e).__name__, e)
        finally:
            conn.close()
