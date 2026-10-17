# 压测工具：模拟平板遥控对本地 web remote 的 /cmd + /state 连发。
# 直连 socket 写死回环 IP：不经代理（urllib 会吃 HTTP_PROXY 环境变量，
# 本机压测可能被代理劫走）、无 URL 拼接面。
import json
import socket
import time

HOST = "127.0.0.1"      # 压测目标恒为本机回环 web remote
PORT = 8765
ACTIONS = ["pause", "resume", "rewind", "next", "prev"]


def _request(payload):
    """一次性 HTTP/1.0 短连接；payload=None 为 GET /state，否则 POST /cmd。"""
    if payload is None:
        head = "GET /state HTTP/1.0\r\n"
        body = b""
    else:
        body = json.dumps(payload).encode()
        head = ("POST /cmd HTTP/1.0\r\n"
                "Content-Type: application/json\r\n"
                "Content-Length: %d\r\n" % len(body))
    req = (head + "Host: %s\r\nConnection: close\r\n\r\n" % HOST).encode() + body
    with socket.create_connection((HOST, PORT), timeout=3) as s:
        s.sendall(req)
        chunks = []
        while True:
            b = s.recv(4096)
            if not b:
                break
            chunks.append(b)
    return b"".join(chunks)


end = time.monotonic() + 400
i = 0
lat = []
while time.monotonic() < end:
    t0 = time.monotonic()
    try:
        _request({"action": ACTIONS[i % len(ACTIONS)]})
        lat.append(time.monotonic() - t0)
    except OSError:
        pass
    try:
        _request(None)
    except OSError:
        pass
    i += 1
    time.sleep(0.8)
print("sent=%d worst_http_latency=%.3fs" % (i, max(lat) if lat else 0))
