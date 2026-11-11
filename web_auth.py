# -*- coding: utf-8 -*-
"""HTTP 边界校验（纯函数，不依赖 Windows/ctypes，macOS/Linux 可直接测）。

两件事：
1. Host 白名单——挡 DNS 重绑定。浏览器同源策略只看 Origin，攻击者把
   自己的域名解析到本机 IP 后，Origin/Host 都是 evil.example，旧
   _cross_origin（Origin==Host）会放行；这里要求 Host 必须是 IP 字面量、
   localhost 或用户显式配置的主机名，域名一律拒。
2. 可选共享令牌——热点/LAN 上任何设备都能调 /cmd 的存量问题。令牌为空
   =保持旧行为（默认，升级不破坏现有遥控）；配置后仅回环地址免令牌，
   其余请求必须带 X-Cube-Token / Bearer / ?t= / Cookie（页面首次用
   ?t= 打开时由服务端种下 HttpOnly Cookie，后续 fetch 自动携带）。
"""
import hmac
import ipaddress
import urllib.parse

LOOPBACK_NAMES = frozenset({"127.0.0.1", "::1", "localhost"})
PUBLIC_PATHS = frozenset({
    "/", "/favicon.ico", "/apple-touch-icon.png", "/app.apk",
})
COOKIE_NAME = "cube_token"
# 原生踩钉转发/诊断不带 Cookie（APP 的 WebView Cookie 不覆盖
# HttpURLConnection）：保留可用性，靠既有私网来源门 + 限流兜底。
NATIVE_PATHS = frozenset({"/pedal/event", "/diag"})


def split_host(host):
    """Host 头 → (hostname 小写, port 或 None, 是否可解析)。"""
    if not host:
        return "", None, False
    try:
        parts = urllib.parse.urlsplit("//" + host.strip())
        name = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return "", None, False
    return name, port, bool(name)


def is_ip_literal(name):
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def host_allowed(host, extra=()):
    """Host 是否可信。空 Host（HTTP/1.0/测试桩）放行——浏览器 HTTP/1.1
    必带 Host，攻击者无法靠省略绕过同源检查。"""
    name, _port, ok = split_host(host)
    if not host:
        return True
    if not ok:
        return False
    if name in LOOPBACK_NAMES or is_ip_literal(name):
        return True
    return name in {str(h).strip().lower() for h in extra if h}


def is_loopback_ip(ip):
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def extract_token(headers, query=""):
    """按优先级取客户端令牌：X-Cube-Token → Bearer → Cookie → ?t=/token。"""
    tok = headers.get("X-Cube-Token") if headers else None
    if tok:
        return tok.strip()
    auth = (headers.get("Authorization") or "") if headers else ""
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    raw = (headers.get("Cookie") or "") if headers else ""
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE_NAME and v:
            return urllib.parse.unquote(v)
    if query:
        q = urllib.parse.parse_qs(query, keep_blank_values=False)
        for key in ("t", "token"):
            if q.get(key):
                return q[key][0].strip()
    return ""


def token_ok(provided, expected):
    if not expected:
        return True
    if not provided:
        return False
    return hmac.compare_digest(str(provided), str(expected))


def is_public_path(path):
    return path in PUBLIC_PATHS or path == "/app" or path.startswith("/app/")


def check_request(host, allowed_hosts, token, client_ip, path, provided):
    """统一边界判定，返回 "ok" / "host" / "token"（纯函数，便于测试）。"""
    if not host_allowed(host, allowed_hosts):
        return "host"
    if not token or is_loopback_ip(client_ip):
        return "ok"
    if path in NATIVE_PATHS or is_public_path(path):
        return "ok"
    if token_ok(provided, token):
        return "ok"
    return "token"
