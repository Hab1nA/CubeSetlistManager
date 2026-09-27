Import-Module "C:\Program Files\Windows MIDI Services\PowerShell\WindowsMidiServices" -ErrorAction Stop
$dll = "C:\Program Files\Windows MIDI Services\PowerShell\WindowsMidiServices\Microsoft.Windows.Devices.Midi2.NetProjection.dll"
$asm = [System.Reflection.Assembly]::LoadFrom($dll)
$ns = "Microsoft.Windows.Devices.Midi2"
function T($name) { $asm.GetType($ns + "." + $name) }

$ediT = T "MidiEndpointDeviceInformation"
$all = $ediT::FindAll()
$target = $all | Where-Object { $_.Name -match "Virtual MIDI Driver" } | Select-Object -First 1
if (-not $target) { Write-Output "未找到 teVirtualMIDI 端点"; exit 1 }
$epid = $target.EndpointDeviceId
Write-Output ("目标端点: " + $target.Name + "  id=" + $epid)

$session = ($sT = T "MidiSession")::Create("winmm-delivery-test")
$conn = $session.CreateEndpointConnection($epid)
if (-not $conn) { Write-Output "连接创建失败"; exit 1 }
$conn.Open()
Write-Output ("连接状态: " + $conn.ConnectionState)

# UMP note-on ch1 C4 vel100（type4 MIDI1.0），随后 note-off
foreach ($w in ([uint32]0x40903C64, [uint32]0x40803C00)) {
    $r = $conn.SendSingleMessageWords([uint64]0, $w)
    Write-Output ("发送 0x" + $w.ToString("X8") + " 结果: " + $r)
    Start-Sleep -Milliseconds 300
}
$conn.Dispose()
$session.Dispose()
Write-Output "发送完成"
