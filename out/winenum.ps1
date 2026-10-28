Add-Type @'
using System; using System.Text; using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr l);
  public delegate bool EnumWindowsProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint wpid);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  public struct RECT { public int L, T, R, B; }
}
'@
$cub = Get-Process | Where-Object { $_.ProcessName -like 'Cubase*' } | Select-Object -ExpandProperty Id
$found = New-Object System.Collections.ArrayList
$cb = {
  param($h, $l)
  [uint32]$wpid2 = 0; [W]::GetWindowThreadProcessId($h, [ref]$wpid2) | Out-Null
  if ($cub -contains [int]$wpid2 -and [W]::IsWindowVisible($h)) {
    $sb = New-Object System.Text.StringBuilder 256
    [W]::GetWindowText($h, $sb, 256) | Out-Null
    $r = New-Object W+RECT; [W]::GetWindowRect($h, [ref]$r) | Out-Null
    $found.Add("$h | '$($sb.ToString())' | L=$($r.L) T=$($r.T) R=$($r.R) B=$($r.B)") | Out-Null
  }
  return $true
}
[W]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null
$found
