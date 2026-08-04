









param([string]$Action, [string]$X, [string]$Y, [string]$D)

Add-Type @"
using System; using System.Runtime.InteropServices;
public class G {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, IntPtr e);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr SetFocus(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, IntPtr pid);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool attach);
  [DllImport("user32.dll")] public static extern uint SendInput(uint n, INPUT[] p, int cb);
  [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT { public ushort wVk, wScan; public uint dwFlags, time; public IntPtr ex; }
  [StructLayout(LayoutKind.Explicit, Size=40)] public struct INPUT { [FieldOffset(0)] public uint type; [FieldOffset(8)] public KEYBDINPUT ki; }
}
"@


$proc = Get-Process Warhammer3 -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -match 'WARHAMMER' } |
        Select-Object -First 1
if (-not $proc) {
  $proc = Get-Process Warhammer3 -ErrorAction SilentlyContinue |
          Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
}


function Force-Foreground($h) {
  if (-not $h -or $h -eq [IntPtr]::Zero) { return $false }
  if ([G]::IsIconic($h)) { [G]::ShowWindow($h, 9) | Out-Null; Start-Sleep -Milliseconds 200 }
  $target = [G]::GetWindowThreadProcessId($h, [IntPtr]::Zero)
  $me = [G]::GetCurrentThreadId()
  [G]::AttachThreadInput($me, $target, $true) | Out-Null
  [G]::BringWindowToTop($h) | Out-Null
  [G]::SetForegroundWindow($h) | Out-Null
  [G]::SetFocus($h) | Out-Null
  [G]::AttachThreadInput($me, $target, $false) | Out-Null
  Start-Sleep -Milliseconds 200
  return ([G]::GetForegroundWindow() -eq $h)
}

$h = if ($proc) { $proc.MainWindowHandle } else { [IntPtr]::Zero }
$focus = Force-Foreground $h


$scan = @{ ESCAPE=0x01; RETURN=0x1C; ENTER=0x1C; SPACE=0x39; TAB=0x0F;
           UP=0x48; DOWN=0x50; LEFT=0x4B; RIGHT=0x4D;



           K=0x25;
           '1'=0x02; '2'=0x03; '3'=0x04; '4'=0x05; '5'=0x06;
           '6'=0x07; '7'=0x08; '8'=0x09; '9'=0x0A; '0'=0x0B }

function SendScan($code) {
  $KEYEVENTF_SCANCODE = 0x0008; $KEYEVENTF_KEYUP = 0x0002
  $down = New-Object G+INPUT; $down.type = 1; $down.ki.wScan = [System.UInt16]$code; $down.ki.dwFlags = $KEYEVENTF_SCANCODE
  $up   = New-Object G+INPUT; $up.type = 1;   $up.ki.wScan = [System.UInt16]$code;   $up.ki.dwFlags = $KEYEVENTF_SCANCODE -bor $KEYEVENTF_KEYUP
  $sz = [Runtime.InteropServices.Marshal]::SizeOf([type]("G+INPUT"))
  [G]::SendInput(1, @($down), $sz) | Out-Null; Start-Sleep -Milliseconds 45
  [G]::SendInput(1, @($up), $sz) | Out-Null
}

$LD=0x02; $LU=0x04; $RD=0x08; $RU=0x10; $MW=0x0800
$ix=0; $iy=0
if ($Action -ne "key") { $ix=[int]$X; $iy=[int]$Y }
function Click($down,$up) {
  [G]::SetCursorPos($ix,$iy) | Out-Null; Start-Sleep -Milliseconds 250
  [G]::mouse_event($down,0,0,0,[IntPtr]::Zero); Start-Sleep -Milliseconds 110
  [G]::mouse_event($up,0,0,0,[IntPtr]::Zero)
}
switch ($Action) {
  "move"   { [G]::SetCursorPos($ix,$iy) | Out-Null }
  "click"  { Click $LD $LU }
  "rclick" { Click $RD $RU }
  "dclick" { Click $LD $LU; Start-Sleep -Milliseconds 120; [G]::mouse_event($LD,0,0,0,[IntPtr]::Zero); Start-Sleep -Milliseconds 80; [G]::mouse_event($LU,0,0,0,[IntPtr]::Zero) }


  "drag"   { $t = "$D" -split ','
             $tx = [int]$t[0]; $ty = [int]$t[1]
             [G]::SetCursorPos($ix,$iy) | Out-Null; Start-Sleep -Milliseconds 250
             [G]::mouse_event($LD,0,0,0,[IntPtr]::Zero); Start-Sleep -Milliseconds 200
             $steps = 14
             for ($i = 1; $i -le $steps; $i++) {
               $mx = $ix + [int](($tx-$ix)*$i/$steps); $my = $iy + [int](($ty-$iy)*$i/$steps)
               [G]::SetCursorPos($mx,$my) | Out-Null; Start-Sleep -Milliseconds 35
             }
             Start-Sleep -Milliseconds 220
             [G]::mouse_event($LU,0,0,0,[IntPtr]::Zero) }


  "wheel"  { [G]::SetCursorPos($ix,$iy) | Out-Null; Start-Sleep -Milliseconds 150
             $n = if ($D) { [int]$D } else { -1 }
             $delta = $n * 120
             $dw = if ($delta -lt 0) { [uint32](4294967296 + $delta) } else { [uint32]$delta }
             [G]::mouse_event($MW,0,0,$dw,[IntPtr]::Zero) }
  "key"    { $c = $scan["$X"]; if (-not $c) { Write-Output "unknown key '$X'"; exit 1 }; SendScan $c }
  default  { Write-Output "usage: click|rclick|dclick|move X Y | wheel X Y N | key NAME"; exit 1 }
}
Write-Output "$Action $X $Y focus=$focus hwnd=$h"
