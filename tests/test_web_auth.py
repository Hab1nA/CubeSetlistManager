# -*- coding: utf-8 -*-
"""web_auth 纯函数 + _Handler 边界门的离线测试。

纯函数部分在 macOS/Linux 可直接跑；handler 部分需要 web_remote（Windows
专有的 ctypes/winmm），非 Windows 平台自动 skip，CI 的 windows-latest 覆盖。
"""
import http.client
import json
import queue
import threading

import pytest

import web_auth


def test_host_allowed_ip_and_loopback():
    assert web_auth.host_allowed("192.168.1.5:8765")
    assert web_auth.host_allowed("127.0.0.1:8766")
    assert web_auth.host_allowed("[::1]:8765")
    assert web_auth.host_allowed("localhost:8765")
    assert web_auth.host_allowed("")          # HTTP/1.0 桩


def test_host_allowed_rejects_dns_rebinding():
    # 攻击者域名解析到本机：Origin/Host 都是 evil.example，旧实现会放行
    assert not web_auth.host_allowed("evil.example:8765")
    assert not web_auth.host_allowed("evil.example")
    assert web_auth.host_allowed("cube.lan:8765", extra=["cube.lan"])


def test_extract_token_priority():
    h = {"X-Cube-Token": "a", "Authorization": "Bearer b",
         "Cookie": "x=1; cube_token=c"}
    assert web_auth.extract_token(h, "t=d") == "a"
    assert web_auth.extract_token({"Authorization": "Bearer b"}, "") == "b"
    assert web_auth.extract_token({"Cookie": "x=1; cube_token=c"}, "") == "c"
    assert web_auth.extract_token({}, "t=d") == "d"
    assert web_auth.extract_token({}, "") == ""


def test_token_ok_constant_time_semantics():
    assert web_auth.token_ok("", "")          # 未配置=不校验
    assert not web_auth.token_ok("", "x")
    assert web_auth.token_ok("x", "x")
    assert not web_auth.token_ok("y", "x")


def test_is_public_path():
    assert web_auth.is_public_path("/")
    assert web_auth.is_public_path("/app")
    assert web_auth.is_public_path("/app/app.js")
    assert not web_auth.is_public_path("/state")
    assert not web_auth.is_public_path("/cmd")


def test_check_request_matrix():
    host = "192.168.1.9:8765"
    assert web_auth.check_request("evil.example", (), "", "8.8.8.8", "/cmd", "") == "host"
    assert web_auth.check_request(host, (), "", "8.8.8.8", "/cmd", "") == "ok"
    assert web_auth.check_request(host, (), "s", "192.168.1.9", "/cmd", "") == "token"
    assert web_auth.check_request(host, (), "s", "192.168.1.9", "/cmd", "s") == "ok"
    assert web_auth.check_request(host, (), "s", "127.0.0.1", "/cmd", "") == "ok"
    assert web_auth.check_request(host, (), "s", "192.168.1.9", "/app", "") == "ok"
    assert web_auth.check_request(host, (), "s", "192.168.1.9", "/pedal/event", "") == "ok"


class _App:
    lite = False

    def __init__(self):
        self._web_snap = {"ok": 1}
        self.q = queue.Queue()


def _server_module():
    try:
        import web_remote
        return web_remote
    except Exception as e:                    # 非 Windows：缺 winmm/winreg
        pytest.skip("web_remote 不可导入：%r" % (e,))


def _serve(**kw):
    wr = _server_module()
    app = _App()
    srv = wr.WebServer(("127.0.0.1", 0), app, object(),
                       lambda: 8766, page="<html>hi</html>", **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _req(srv, method, path, host=None, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1],
                                      timeout=5)
    if host is None:
        conn.request(method, path, body=body, headers=headers or {})
    else:
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host)
        for k, v in (headers or {}).items():
            conn.putheader(k, v)
        if body is not None:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        if body is not None:
            conn.send(body)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, r.getheaders(), data


def test_host_header_gate_rejects_rebinding():
    srv = _serve()
    try:
        st, _, _ = _req(srv, "GET", "/", host="evil.example")
        assert st == 403
        st, _, body = _req(srv, "GET", "/", host="127.0.0.1")
        assert st == 200 and body == b"<html>hi</html>"
    finally:
        srv.shutdown()
        srv.server_close()


def test_page_sets_httponly_cookie_from_query():
    srv = _serve(auth_token="s3cret")
    try:
        st, hdrs, _ = _req(srv, "GET", "/?t=s3cret", host="127.0.0.1")
        assert st == 200
        cookies = [v for k, v in hdrs if k.lower() == "set-cookie"]
        assert cookies and "cube_token=s3cret" in cookies[0]
        assert "HttpOnly" in cookies[0]
    finally:
        srv.shutdown()
        srv.server_close()


def test_post_gate_blocks_cross_origin_before_body():
    srv = _serve()
    try:
        st, _, _ = _req(srv, "POST", "/cmd", host="127.0.0.1",
                        headers={"Origin": "http://evil.example"},
                        body=json.dumps({"cmd": "next"}).encode())
        assert st == 403
    finally:
        srv.shutdown()
        srv.server_close()


def test_turn_body_carries_token():
    """authToken 配置后 /turn 推送体带 t 字段（设备端监听口据此鉴权），
    未配置不携带——旧安卓端 optString 解析对多余字段无感。"""
    wr = _server_module()
    assert json.loads(wr._turn_body("next", False, "")) == {"dir": "next"}
    assert json.loads(wr._turn_body("prev", True, "s")) == \
        {"dir": "prev", "test": 1, "t": "s"}
    # 镜像默认值：模块级 _turn_token 未配置=空
    assert json.loads(wr._turn_body("next", False)) == {"dir": "next"}
