# Capture the Warhammer3 window (or primary screen) to a PNG.
# Usage: powershell -File capture.ps1 <outputPath>
param([Parameter(Mandatory=$true)][string]$Out)

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win {
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool GetCursorInfo(ref CURSORINFO pci);
  [DllImport("user32.dll")] public static extern bool GetIconInfo(IntPtr hIcon, out ICONINFO pi);
  [DllImport("user32.dll")] public static extern bool DrawIconEx(IntPtr hdc, int x, int y, IntPtr hIcon, int cx, int cy, int step, IntPtr brush, int flags);
  public struct RECT { public int Left, Top, Right, Bottom; }
  public struct POINT { public int X, Y; }
  public struct CURSORINFO { public int cbSize; public int flags; public IntPtr hCursor; public POINT ptScreenPos; }
  public struct ICONINFO { public bool fIcon; public int xHotspot; public int yHotspot; public IntPtr hbmMask; public IntPtr hbmColor; }
}
"@
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$p = Get-Process Warhammer3 -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if ($p) {
  $h = $p.MainWindowHandle
  if ([Win]::IsIconic($h)) { [Win]::ShowWindow($h, 9) | Out-Null; Start-Sleep -Milliseconds 400 }
  [Win]::SetForegroundWindow($h) | Out-Null
  Start-Sleep -Milliseconds 250
  # Capture the CLIENT area (game content only, no title bar / borders) so pixel fractions
  # are consistent whether the window is borderless-fullscreen or a bordered window.
  $r = New-Object Win+RECT
  [Win]::GetClientRect($h, [ref]$r) | Out-Null
  $pt = New-Object Win+POINT
  [Win]::ClientToScreen($h, [ref]$pt) | Out-Null
  $x = $pt.X; $y = $pt.Y; $w = $r.Right - $r.Left; $hh = $r.Bottom - $r.Top
  $src = "window '$($p.MainWindowTitle)' client=($x,$y $($w)x$($hh))"
} else {
  $vs = [System.Windows.Forms.SystemInformation]::PrimaryMonitorSize
  $x = 0; $y = 0; $w = $vs.Width; $hh = $vs.Height
  $src = "no game window; primary screen $($w)x$($hh)"
}
if ($w -le 0 -or $hh -le 0) { Write-Output "bad rect: $src"; exit 1 }
$bmp = New-Object System.Drawing.Bitmap($w, $hh)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($x, $y, 0, 0, (New-Object System.Drawing.Size($w, $hh)))
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)

# --- frame-content stats (downscaled 48x27): luma / colour-saturation / busyness -----------
# A Windows screensaver / wallpaper-slideshow was seen cycling FULL-SCREEN images (solid black,
# a mono Games-Workshop logo, a hyper-saturated "Intel Core" image) OVER the borderless game
# whenever it lost foreground, so CopyFromScreen grabbed the OVERLAY, not the game -- poisoning
# every vision gate. These three cheap statistics let the launcher REJECT such non-game frames
# before trusting any diff (measured: black luma~2.6; mono logo sat~5.6; intel sat~123; real WH3
# frames luma~81 sat~19 busy~11.5). Printed as an extra stdout line; the caller regex-parses it.
$SW = 48; $SH = 27
$small = New-Object System.Drawing.Bitmap($SW, $SH)
$sg = [System.Drawing.Graphics]::FromImage($small)
$sg.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$sg.DrawImage($bmp, (New-Object System.Drawing.Rectangle 0, 0, $SW, $SH),
              (New-Object System.Drawing.Rectangle 0, 0, $w, $hh),
              [System.Drawing.GraphicsUnit]::Pixel)
$sg.Dispose()
$lsum = 0.0; $ssum = 0.0
$luma = New-Object 'double[]' ($SW * $SH)
for ($yy = 0; $yy -lt $SH; $yy++) {
  for ($xx = 0; $xx -lt $SW; $xx++) {
    $px = $small.GetPixel($xx, $yy)
    $rr = [int]$px.R; $gg = [int]$px.G; $bb = [int]$px.B
    $lv = 0.299 * $rr + 0.587 * $gg + 0.114 * $bb
    $luma[$yy * $SW + $xx] = $lv
    $lsum += $lv
    $mx = [Math]::Max($rr, [Math]::Max($gg, $bb)); $mn = [Math]::Min($rr, [Math]::Min($gg, $bb))
    $ssum += ($mx - $mn)
  }
}
$bsum = 0.0; $bcnt = 0
for ($yy = 0; $yy -lt $SH; $yy++) {
  for ($xx = 0; $xx -lt ($SW - 1); $xx++) {
    $bsum += [Math]::Abs($luma[$yy * $SW + $xx] - $luma[$yy * $SW + $xx + 1]); $bcnt++
  }
}
$small.Dispose()
$lm = $lsum / ($SW * $SH); $satm = $ssum / ($SW * $SH); $busy = $bsum / $bcnt
Write-Output ("stats=(luma={0:F2} sat={1:F2} busy={2:F2})" -f $lm, $satm, $busy)

# --- letterbox band stats (P7 cutscene corroboration): mean luma of the top / mid / bottom
# row bands of the SAME 48x27 downscale. A playing cinematic LETTERBOXES the frame (near-black
# bars top+bottom, ~12% each, around a lit picture) -- a signature the whole-frame stats above
# cannot see. Rows 0-2 and 24-26 sit fully inside typical letterbox bars; rows 10-16 are the
# picture. Printed as one extra stdout line; the caller regex-parses it (extra lines are inert
# to the existing client=/stats= parsers).
$tsum = 0.0; $msum = 0.0; $botsum = 0.0
for ($xx = 0; $xx -lt $SW; $xx++) {
  for ($yy = 0; $yy -le 2; $yy++)   { $tsum += $luma[$yy * $SW + $xx] }
  for ($yy = 10; $yy -le 16; $yy++) { $msum += $luma[$yy * $SW + $xx] }
  for ($yy = 24; $yy -le 26; $yy++) { $botsum += $luma[$yy * $SW + $xx] }
}
$topm = $tsum / (3 * $SW); $midm = $msum / (7 * $SW); $botm = $botsum / (3 * $SW)
Write-Output ("bands=(top={0:F2} mid={1:F2} bot={2:F2})" -f $topm, $midm, $botm)

$g.Dispose(); $bmp.Dispose()
Write-Output "$src -> $Out"
