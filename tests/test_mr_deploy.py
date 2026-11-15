# -*- coding: utf-8 -*-
"""Cubase 直控部署助手测试（零设备零注册表真写——winreg/进程/枚举全打桩，
风格同 test_mr_ctrl 假 winmm 口径）。"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import mr_deploy

_SRC_JS = (pathlib.Path(__file__).resolve().parents[1] / "mr_driver"
           / "Local" / "CubeSetlistManager" / "CubeSetlistRemote"
           / "CubeSetlistManager_CubeSetlistRemote.js")


@pytest.fixture
def tmp_dst(monkeypatch, tmp_path):
    dst = tmp_path / "Steinberg" / "Cubase" / "MIDI Remote" / "Driver Scripts" \
        / mr_deploy._LOCAL
    monkeypatch.setattr(mr_deploy, "driver_dst", lambda: dst)
    return dst


def test_deploy_copies_and_is_idempotent(tmp_dst):
    assert mr_deploy.deploy_driver() == ""
    js = list(tmp_dst.glob("*.js"))
    assert [p.name for p in js] == [_SRC_JS.name]
    assert js[0].read_bytes() == _SRC_JS.read_bytes()
    m = js[0].stat().st_mtime_ns
    assert mr_deploy.deploy_driver() == ""
    assert js[0].stat().st_mtime_ns == m


def test_deploy_src_missing(tmp_dst, monkeypatch):
    monkeypatch.setattr(mr_deploy, "driver_src",
                        lambda: pathlib.Path("Z:/no/such/dir"))
    assert "缺失" in mr_deploy.deploy_driver()
    assert not tmp_dst.exists()


def test_deploy_write_error(tmp_dst, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(pathlib.Path, "mkdir", boom)
    assert "部署失败" in mr_deploy.deploy_driver()


class _FakeWinreg:
    """winreg 替身：记录 CreateKey/SetValueEx 调用，可选抛 OSError。"""

    HKEY_CURRENT_USER = 0x80000001
    REG_DWORD = 3

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def CreateKey(self, *a):
        self.calls.append(("create", a[1] if len(a) > 1 else None))
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def SetValueEx(self, k, name, *rest):
        if self.fail:
            raise OSError("access denied")
        self.calls.append(("set", name))


def _fake_env(monkeypatch, port_seq, loop_up=False, wr=None):
    monkeypatch.setattr(mr_deploy, "port_ok",
                        lambda: bool(port_seq and next(port_seq)))
    monkeypatch.setattr(mr_deploy, "find_processes_by_prefix",
                        lambda p: [(1, "loopMIDI.exe")] if loop_up else [])
    monkeypatch.setattr(mr_deploy, "loopmidi_exe", lambda: "C:/l.exe")
    monkeypatch.setattr(mr_deploy, "close_loopmidi", lambda: True)
    monkeypatch.setattr(mr_deploy, "launch_detached", lambda e: 33)
    if wr is not None:
        monkeypatch.setattr(mr_deploy, "winreg", wr)


def test_create_port_skips_when_present(monkeypatch):
    wr = _FakeWinreg()
    _fake_env(monkeypatch, port_seq=iter([1]), wr=wr)
    assert mr_deploy.create_port() == ""
    assert wr.calls == []


def test_create_port_full_flow(monkeypatch):
    wr = _FakeWinreg()
    port_seq = iter([False, False, True])
    _fake_env(monkeypatch, port_seq=port_seq, loop_up=True, wr=wr)
    assert mr_deploy.create_port() == ""
    assert ("set", mr_deploy.PORT_NAME) in wr.calls


def test_create_port_no_exe(monkeypatch):
    wr = _FakeWinreg()
    _fake_env(monkeypatch, port_seq=iter([False]), wr=wr)
    monkeypatch.setattr(mr_deploy, "loopmidi_exe", lambda: "")
    assert "loopMIDI" in mr_deploy.create_port()
    # 注册表已写入（下次装好 loopMIDI 后重启即可生效，写值不回滚）
    assert ("set", mr_deploy.PORT_NAME) in wr.calls


def test_create_port_reg_fail(monkeypatch):
    wr = _FakeWinreg(fail=True)
    _fake_env(monkeypatch, port_seq=iter([False, True]), wr=wr)
    assert "注册表" in mr_deploy.create_port()
    assert not any(c[0] == "set" for c in wr.calls)


_S1_SRC = (pathlib.Path(__file__).resolve().parents[1] / "s1_driver"
           / "CubeSetlistManager" / "CubeSetlistRemote")


@pytest.fixture
def tmp_s1_dst(monkeypatch, tmp_path):
    dst = tmp_path / "PreSonus" / "Studio One 7" / "User Devices"         / "User Devices"
    monkeypatch.setattr(mr_deploy, "s1_dst", lambda: dst)
    return dst


def test_s1_deploy_copies_all_and_idempotent(tmp_s1_dst):
    assert mr_deploy.deploy_s1_driver() == ""
    files = sorted(p.name for p in tmp_s1_dst.iterdir())
    src_names = sorted(p.name for p in _S1_SRC.iterdir() if p.is_file())
    assert files == src_names
    assert (tmp_s1_dst / "CubeSetlistRemote.surface.xml").read_bytes() ==         (_S1_SRC / "CubeSetlistRemote.surface.xml").read_bytes()
    m = (tmp_s1_dst / "CubeSetlistRemote.device").stat().st_mtime_ns
    assert mr_deploy.deploy_s1_driver() == ""
    assert (tmp_s1_dst / "CubeSetlistRemote.device").stat().st_mtime_ns == m


def test_s1_deploy_src_missing(tmp_s1_dst, monkeypatch):
    monkeypatch.setattr(mr_deploy, "s1_driver_src",
                        lambda: pathlib.Path("Z:/no/such/dir"))
    assert "缺失" in mr_deploy.deploy_s1_driver()
    assert not tmp_s1_dst.exists()


def test_s1_status_fields(tmp_s1_dst, monkeypatch):
    mr_deploy.deploy_s1_driver()
    monkeypatch.setattr(mr_deploy, "port_ok", lambda: True)
    monkeypatch.setattr(mr_deploy, "loopmidi_exe", lambda: "C:/l.exe")
    monkeypatch.setattr(mr_deploy, "find_processes_by_prefix",
                        lambda p: [(9, "S1.exe")] if "studio" in p else [])
    st = mr_deploy.s1_status()
    assert st == {"src": True, "script": True, "stale": False, "port": True,
                  "loopmidi": True, "daw": True}


def test_status_fields(tmp_dst, monkeypatch):
    mr_deploy.deploy_driver()
    monkeypatch.setattr(mr_deploy, "port_ok", lambda: True)
    monkeypatch.setattr(mr_deploy, "loopmidi_exe", lambda: "C:/l.exe")
    monkeypatch.setattr(mr_deploy, "find_processes_by_prefix",
                        lambda p: [(9, "Cubase15.exe")] if p == "cubase" else [])
    st = mr_deploy.status("cubase")
    assert st == {"src": True, "script": True, "stale": False, "port": True,
                  "loopmidi": True, "daw": True}
