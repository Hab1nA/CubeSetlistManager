# -*- coding: utf-8 -*-
"""obs-websocket v5 的最小 WebSocket 客户端（纯标准库，离线可用）。

只实现桥接需要的子集：ws:// 明文（连本机 OBS）、文本帧、客户端掩码、
扩展长度、ping 自动应答。不含 TLS / 分片 / 压缩（ponytail 天花板：
本机直连 OBS 用不上；要跨机 TLS 时换真 websocket 库）。

协议（github.com/obsproject/obs-websocket，OBS 28+ 内置）：
  Hello(op0) → Identify(op1) → Identified(op2)；Request(op6)/RequestResponse(op7)
  认证串 = base64(sha256( base64(sha256(密码+salt)) + challenge ))
"""
import base64
import hashlib
import json
import os
import socket
import struct

OP_HELLO, OP_IDENTIFY, OP_IDENTIFIED = 0, 1, 2
OP_EVENT, OP_REQUEST, OP_REQUEST_RESPONSE = 5, 6, 7


class ObsError(Exception):
    pass


def auth_digest(password, salt, challenge):
    """obs-websocket v5 质询应答（文档规定的双重 sha256+base64 串）。"""
    secret = base64.b64encode(
        hashlib.sha256((password + salt).encode("utf-8")).digest()).decode()
    return base64.b64encode(
        hashlib.sha256((secret + challenge).encode("utf-8")).digest()).decode()


def encode_text_frame(payload, mask_key):
    """客户端帧必须掩码：b1=0x81(text|FIN)，长度 7/16/64 位，负载异或掩码。"""
    assert len(mask_key) == 4
    head = bytearray([0x81])
    n = len(payload)
    if n < 126:
        head.append(0x80 | n)
    elif n < (1 << 16):
        head.append(0x80 | 126)
        head += struct.pack(">H", n)
    else:
        head.append(0x80 | 127)
        head += struct.pack(">Q", n)
    head += mask_key
    masked = bytearray(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return bytes(head) + bytes(masked)


def decode_frame(buf, pos):
    """解析服务端帧（不掩码）。返回 (opcode, payload, 下一帧起点)。
    帧头/帧体不完整时抛 ObsError——调用方 _read_frame 靠它继续攒字节。"""
    if len(buf) - pos < 2:
        raise ObsError("帧头不完整")
    op = buf[pos] & 0x0F
    n = buf[pos + 1] & 0x7F
    p = pos + 2
    if n == 126:
        if len(buf) - p < 2:
            raise ObsError("帧头不完整（16 位长度缺失）")
        n = struct.unpack(">H", buf[p:p + 2])[0]
        p += 2
    elif n == 127:
        if len(buf) - p < 8:
            raise ObsError("帧头不完整（64 位长度缺失）")
        n = struct.unpack(">Q", buf[p:p + 8])[0]
        p += 8
    if buf[pos + 1] & 0x80:  # 服务端帧不应掩码，容错处理
        if len(buf) - p < n + 4:
            raise ObsError("帧体不完整")
        mask = buf[p:p + 4]
        p += 4
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(buf[p:p + n]))
    else:
        if len(buf) - p < n:
            raise ObsError("帧体不完整")
        payload = bytes(buf[p:p + n])
    return op, payload, p + n


class ObsWs:
    """一条已认证的 obs-websocket 连接。线程不安全，由持有方加锁。"""

    def __init__(self, host, port, password="", timeout=5):
        self.password = password
        self._buf = b""
        self._seq = 0
        self.sock = socket.create_connection((host, port), timeout)
        self.sock.settimeout(timeout)
        self._handshake(host, port)
        self._identify()

    # ---- WebSocket 底层 ----

    def _handshake(self, host, port):
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET / HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
               "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n" % (host, port, key))
        self.sock.sendall(req.encode())
        while b"\r\n\r\n" not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ObsError("握手时连接断开")
            self._buf += chunk
        head, _, self._buf = self._buf.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ObsError("WebSocket 握手被拒：%r" % head[:80])

    def _recv_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ObsError("连接断开")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _read_frame(self):
        """积攒字节直到一帧完整：ping 回 pong、close 抛错，其余返回 JSON。"""
        while True:
            if self._buf:
                try:
                    op, payload, nxt = decode_frame(self._buf, 0)
                except (ObsError, IndexError, struct.error):
                    op = None  # 帧还没收全，继续攒
            else:
                op, nxt = None, 0
            if op is not None:
                if op == 0x9:  # ping → 回 pong（客户端帧需掩码）
                    self.sock.sendall(encode_text_frame(payload, os.urandom(4)))
                    self._buf = self._buf[nxt:]
                    continue
                if op == 0x8:
                    raise ObsError("服务端关闭连接")
                self._buf = self._buf[nxt:]
                return json.loads(payload.decode("utf-8"))
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ObsError("连接断开")
            self._buf += chunk

    def _send_json(self, obj):
        self.sock.sendall(encode_text_frame(
            json.dumps(obj, ensure_ascii=False).encode("utf-8"), os.urandom(4)))

    # ---- obs-websocket v5 ----

    def _identify(self):
        hello = self._read_frame()
        if hello.get("op") != OP_HELLO:
            raise ObsError("首帧不是 Hello：%r" % str(hello)[:80])
        d = hello.get("d", {})
        ident = {"rpcVersion": 1, "eventSubscriptions": 0}
        if "challenge" in d:  # 设了密码才需要认证
            if not self.password:
                raise ObsError("OBS 设了 WebSocket 密码，请在 config.json obs.password 填写")
            ident["authentication"] = auth_digest(
                self.password, d["salt"], d["challenge"])
        self._send_json({"op": OP_IDENTIFY, "d": ident})
        ok = self._read_frame()
        if ok.get("op") != OP_IDENTIFIED:
            raise ObsError("Identify 被拒（密码错？）：%r" % str(ok)[:80])

    def request(self, request_type, data=None, timeout=5):
        """发一个请求，跳过事件帧等到对应响应。返回 responseData。"""
        self._seq += 1
        rid = "q%d" % self._seq
        req = {"op": OP_REQUEST, "d": {"requestType": request_type,
                                       "requestId": rid}}
        if data is not None:
            req["d"]["requestData"] = data
        self.sock.settimeout(timeout)
        self._send_json(req)
        while True:
            msg = self._read_frame()
            if msg.get("op") != OP_REQUEST_RESPONSE:
                continue  # 未订阅事件，这里基本只有响应；保险起见跳过
            d = msg.get("d", {})
            if d.get("requestId") != rid:
                continue
            st = d.get("requestStatus", {})
            if not st.get("result", False):
                raise ObsError("%s 失败：%s" % (request_type, st.get("comment", "")))
            return d.get("responseData") or {}

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
