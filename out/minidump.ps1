param([int]$ProcId, [string]$DumpPath)
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class Dmp {
  [DllImport("dbghelp.dll", SetLastError=true)]
  public static extern bool MiniDumpWriteDump(IntPtr hProc, int pid, IntPtr file,
      int type, IntPtr exc, IntPtr user, IntPtr cb);
  [DllImport("kernel32.dll", SetLastError=true)]
  public static extern IntPtr OpenProcess(int access, bool inherit, int pid);
  [DllImport("kernel32.dll")]
  public static extern bool CloseHandle(IntPtr h);
}
'@
$h = [Dmp]::OpenProcess(0x1F0FFF, $false, $ProcId)
if ($h -eq [IntPtr]::Zero) { Write-Output 'OpenProcess failed'; exit 1 }
$fs = [System.IO.File]::Create($DumpPath)
$ok = [Dmp]::MiniDumpWriteDump($h, $ProcId, $fs.Handle, 0x00000002, [IntPtr]::Zero, [IntPtr]::Zero, [IntPtr]::Zero)
$fs.Close()
[Dmp]::CloseHandle($h) | Out-Null
$len = (Get-Item $DumpPath).Length
Write-Output ("dump ok=" + $ok + " size=" + $len)
