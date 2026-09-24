# -*- coding: utf-8 -*-
"""Windows 移动热点管理：PowerShell 子进程调 WinRT
NetworkOperatorTetheringManager（旧式 netsh wlan hostednetwork 不用）。
脚本字符串内置（-EncodedCommand 传递，免疫引号/编码问题），不新增分发文件。
state() 查询；ensure_on() 确保开启（已开=只查，返回 was_on 供所有权判断）；
stop() 关闭。全部阻塞式（PS 子进程 1–10 秒），调用方必须在工作线程。
无无线网卡/无已连接网络/被策略禁用分别给出明确报错（设计第三节）。"""
import base64
import json
import os
import subprocess

# mode 由环境变量 CUBE_HS_MODE 传入（state/start/stop）；输出强制 UTF-8，
# 最后一行是单行 JSON（前面可能有杂散输出，解析时取最后一个 { 开头行）。
_PS = r"""
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
function Emit([hashtable]$o) { $o | ConvertTo-Json -Compress }
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $null = [Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,ContentType=WindowsRuntime]
  $null = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]
  $net = [Windows.Networking.Connectivity.NetworkInformation]
  $nop = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]

  # WinRT IAsyncOperation → 同步等待（PS 5.1 无 await）
  $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]

  # 逐个已连接的网络配置文件查热点能力（机制跟随系统，不钉具体网卡名）
  $mgr = $null; $lastCap = $null
  foreach ($p in $net::GetConnectionProfiles()) {
    $cap = $nop::GetTetheringCapabilityFromConnectionProfile($p)
    if ("$cap" -eq 'Enabled') {
      $mgr = $nop::CreateFromConnectionProfile($p)
      break
    }
    $lastCap = $cap
  }
  if ($null -eq $mgr) {
    $msg = switch ("$lastCap") {
      'DisabledByHardwareLimitation' { '无线网卡不支持开热点（硬件限制）' }
      'DisabledByGroupPolicy'        { '热点被组策略禁用' }
      'DisabledBySku'                { '网卡驱动/系统版本不支持热点' }
      'DisabledByHardwareOperating'  { '网卡驱动未就绪' }
      default { '没有支持热点的已连接网络：先连任意网络（WiFi/网线）再试' }
    }
    Emit @{ok=$false; err=$msg; detail="$lastCap"}
    exit
  }

  function Get-ApIp {
    # 热点网卡=Microsoft Wi-Fi Direct 虚拟适配器；驱动描述本地化不影响匹配
    try {
      $ads = Get-NetAdapter -ErrorAction Stop | Where-Object {
        $_.InterfaceDescription -match 'Wi-Fi Direct' -and
        $_.Status -ne 'Disabled' }
      foreach ($a in $ads) {
        $ip = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $a.ifIndex `
                -ErrorAction Stop | Select-Object -First 1 -ExpandProperty IPAddress
        if ($ip) { return $ip }
      }
    } catch {}
    try {
      # 兜底：ICS 默认网段在本机的地址（设计允许的回退，非唯一来源）
      Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
        Where-Object { $_.IPAddress -like '192.168.137.*' } |
        Select-Object -First 1 -ExpandProperty IPAddress
    } catch {}
    return $null
  }

  $mode = $env:CUBE_HS_MODE
  $cfg = $mgr.GetCurrentAccessPointConfiguration()
  $res = @{ok=$true; on=("$($mgr.TetheringOperationalState)" -eq 'On');
           ssid=[string]$cfg.Ssid; key=[string]$cfg.Passphrase}

  if ($mode -eq 'stop') {
    $t = $asTask.MakeGenericMethod(
      [Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
    $op = $t.Invoke($null, @($mgr.StopTetheringAsync()))
    $op.Wait(-1) | Out-Null
    $r = $op.Result
    if ("$($r.Status)" -ne 'Success') {
      Emit @{ok=$false; err=("关热点失败：" + "$($r.Status)" +
        $(if ($r.AdditionalErrorMessage) { "（" + $r.AdditionalErrorMessage + "）" } else { "" }))}
      exit
    }
    $res.on = $false; $res.ip = $null
    Emit $res
    exit
  }

  if ($mode -eq 'start' -and "$($mgr.TetheringOperationalState)" -ne 'On') {
    $t = $asTask.MakeGenericMethod(
      [Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
    $op = $t.Invoke($null, @($mgr.StartTetheringAsync()))
    $op.Wait(-1) | Out-Null
    $r = $op.Result
    if ("$($r.Status)" -ne 'Success') {
      $msg = "开热点失败：" + "$($r.Status)" +
        $(if ($r.AdditionalErrorMessage) { "（" + $r.AdditionalErrorMessage + "）" } else { "" })
      Emit @{ok=$false; err=$msg}
      exit
    }
    $res.started = $true
    $res.on = $true
    $cfg = $mgr.GetCurrentAccessPointConfiguration()
    $res.ssid = [string]$cfg.Ssid
    $res.key = [string]$cfg.Passphrase
  }
  $res.ip = Get-ApIp
  Emit $res
} catch {
  Emit @{ok=$false; err='PowerShell/WinRT 异常：' + $_.Exception.Message}
}
"""


def _err(e):
    return str(e).strip() or type(e).__name__


def _parse(out):
    for line in reversed(out.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                v = json.loads(line)
                if isinstance(v, dict):
                    return v
            except ValueError:
                pass
    return None


def _run(mode, timeout):
    """跑 PS 脚本，返回脚本输出的 JSON dict；无输出/超时=结构化失败。"""
    enc = base64.b64encode(_PS.encode("utf-16-le")).decode("ascii")
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
            capture_output=True, timeout=timeout,
            env=dict(os.environ, CUBE_HS_MODE=mode))
    except subprocess.TimeoutExpired:
        return {"ok": False, "err": "PowerShell 调用超时（%ds）" % timeout}
    except OSError as e:
        return {"ok": False, "err": "无法启动 PowerShell：%s" % _err(e)}
    r = _parse(p.stdout.decode("utf-8", "replace"))
    if r is None:
        tail = (p.stderr.decode("utf-8", "replace").strip()
                .splitlines() or [""])[-1]
        return {"ok": False, "err": "热点脚本无输出（exit %s）%s"
                % (p.returncode, "：" + tail if tail else "")}
    return r


def state():
    """热点状态：{ok, on, ssid, key, ip}（未开时 ip 为 null）或 {ok:False, err}。"""
    return _run("state", timeout=30)


def ensure_on():
    """确保热点开启（已开=只查）。返回 {ok, on, was_on, ssid, key, ip}；
    was_on=True 表示调用前就已开启（调用方据此决定退出时要不要替用户关）。"""
    st = state()
    if not st.get("ok"):
        return st
    if st.get("on"):
        st["was_on"] = True
        return st
    r = _run("start", timeout=60)
    if not r.get("ok"):
        return r
    r["was_on"] = False
    return r


def stop():
    return _run("stop", timeout=30)
