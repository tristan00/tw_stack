# Force the Warhammer3 window RESTORED + MAXIMIZED + FOREGROUND, robust to the minimized
# state (a minimized WH3 can report MainWindowHandle=0, so enumerate ALL top-level windows
# owned by the process). WH3 pauses its sim + the mod's poll while minimized/background,
# which looks like a crash. Call before every UI operation.
Add-Type @"
using System; using System.Collections.Generic; using System.Runtime.InteropServices;
public class Win {
  public delegate bool EnumProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr p);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool attach);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SystemParametersInfo(uint a, uint b, IntPtr c, uint d);
  [DllImport("user32.dll")] public static extern bool AllowSetForegroundWindow(int pid);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);
  public struct RECT { public int L, T, R, B; }
  public static void KeepAwake() {
    // Defeat the screensaver / wallpaper-slideshow that was overlaying the borderless game and
    // poisoning screen captures. SPI_SETSCREENSAVEACTIVE(0x0011)=FALSE turns the screensaver OFF
    // for this session (fWinIni=0 => change the running value only, don't persist to the profile).
    SystemParametersInfo(0x0011, 0, IntPtr.Zero, 0);
    // ES_CONTINUOUS|ES_DISPLAY_REQUIRED|ES_SYSTEM_REQUIRED: keep the display on so neither the
    // screensaver nor display-sleep engages while we drive the UI.
    SetThreadExecutionState(0x80000000u | 0x00000002u | 0x00000001u);
  }
  static uint _pid;
  static IntPtr _found = IntPtr.Zero;
  static long _best = 0;
  public static IntPtr Find(uint targetPid) {
    // Pick the LARGEST top-level window of the process, not the first titled one -- the game
    // spawns a tiny 1x1 splash/helper window that would otherwise be grabbed (breaking coords).
    _pid = targetPid; _found = IntPtr.Zero; _best = 0;
    EnumWindows((h, p) => {
      uint pid; GetWindowThreadProcessId(h, out pid);
      if (pid == _pid && GetWindowTextLength(h) > 0) {
        RECT r; if (GetWindowRect(h, out r)) {
          long area = (long)(r.R - r.L) * (r.B - r.T);
          if (area > _best) { _best = area; _found = h; }
        }
      }
      return true;
    }, IntPtr.Zero);
    return _found;
  }
  public static string Raise(IntPtr h) {
    // ONLY un-minimize if minimized (SW_RESTORE=9). Do NOT maximize -- the game runs
    // borderless-fullscreen at the calibrated 2560x1440; ShowWindow(SW_MAXIMIZE) forces it
    // into a windowed-maximized mode (title bar -> 1369 client, white render) that breaks
    // both the display and every calibrated coordinate.
    if (IsIconic(h)) { ShowWindow(h, 9); System.Threading.Thread.Sleep(300); }
    System.Threading.Thread.Sleep(50);
    // Defeat Windows' foreground-lock: setting SPI_SETFOREGROUNDLOCKTIMEOUT(0x2001)=0 lets
    // SetForegroundWindow actually raise a window we don't own the input queue of (otherwise
    // it just flashes the taskbar and the game stays behind Steam/the browser).
    SystemParametersInfo(0x2001, 0, IntPtr.Zero, 0x2);
    uint tpid; uint tthread = GetWindowThreadProcessId(h, out tpid);
    AllowSetForegroundWindow(-1);
    uint me = GetCurrentThreadId();
    AttachThreadInput(me, tthread, true);
    BringWindowToTop(h); ShowWindow(h, 5); SetForegroundWindow(h);
    AttachThreadInput(me, tthread, false);
    System.Threading.Thread.Sleep(200);
    return (GetForegroundWindow() == h) ? "foreground" : "restored-not-fg";
  }
}
"@
[Win]::KeepAwake()   # disable screensaver + keep display awake before any UI operation
$proc = Get-Process Warhammer3 -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $proc) { Write-Output "no-process"; exit 0 }
$h = [Win]::Find([uint32]$proc.Id)
if ($h -eq [IntPtr]::Zero) { Write-Output "no-window"; exit 0 }
Write-Output ([Win]::Raise($h))
