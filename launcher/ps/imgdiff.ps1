# Mean absolute per-channel difference between two PNGs, downscaled to 48x27 so that
# animated-but-static screens (falling snow / drifting aurora on the WH3 main menu) average
# out to a tiny delta while a genuine SCREEN CHANGE (menu -> campaign select -> map) produces
# a large delta. Prints a single number in [0,255] on the last line (higher = more different).
# Usage: powershell -File imgdiff.ps1 <pngA> <pngB> [fl ft fr fb]
#   fl ft fr fb (optional) = crop the SAME sub-rectangle of BOTH frames before comparing,
#   given as FRACTIONS of the frame (left,top,right,bottom in 0..1). This is how the launcher
#   gates the WH3 main-menu accordion: clicking "Campaign" only repaints the LEFT column
#   (New/Load appear), a change the full-frame diff (~0.4) is blind to but a left-column crop
#   (fl=0 ft=0 fr=0.18 fb=1) sees clearly (~2.5 vs ~0.03 snow animation). Omit for full frame.
# NB: PowerShell variables are case-INSENSITIVE, so params are named $PathA/$PathB (never
# $A/$B) to avoid aliasing the local pixel/bitmap variables.
param([Parameter(Mandatory=$true)][string]$PathA, [Parameter(Mandatory=$true)][string]$PathB,
      [double]$FL = 0.0, [double]$FT = 0.0, [double]$FR = 1.0, [double]$FB = 1.0)
Add-Type -AssemblyName System.Drawing
$SW = 48; $SH = 27
function Get-Small([string]$Path) {
  $img = [System.Drawing.Image]::FromFile($Path)
  # Source sub-rectangle (whole image by default; a fractional crop when fl/ft/fr/fb given).
  $iw = $img.Width; $ih = $img.Height
  $cx = [int]([Math]::Round($FL * $iw)); $cy = [int]([Math]::Round($FT * $ih))
  $cw = [int]([Math]::Round(($FR - $FL) * $iw)); $ch = [int]([Math]::Round(($FB - $FT) * $ih))
  if ($cw -lt 1) { $cw = 1 }; if ($ch -lt 1) { $ch = 1 }
  $small = New-Object System.Drawing.Bitmap $SW, $SH
  $gfx = [System.Drawing.Graphics]::FromImage($small)
  $gfx.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $dst = New-Object System.Drawing.Rectangle 0, 0, $SW, $SH
  $srcR = New-Object System.Drawing.Rectangle $cx, $cy, $cw, $ch
  $gfx.DrawImage($img, $dst, $srcR, [System.Drawing.GraphicsUnit]::Pixel)
  $gfx.Dispose()
  $img.Dispose()
  return $small
}
try {
  $bmpA = Get-Small $PathA
  $bmpB = Get-Small $PathB
} catch {
  Write-Output "-1"   # unreadable/absent frame -> caller reads as "unknown"
  exit 0
}
$sum = 0.0
for ($yy = 0; $yy -lt $SH; $yy++) {
  for ($xx = 0; $xx -lt $SW; $xx++) {
    $pa = $bmpA.GetPixel($xx, $yy)
    $pb = $bmpB.GetPixel($xx, $yy)
    $sum += [Math]::Abs([int]$pa.R - [int]$pb.R)
    $sum += [Math]::Abs([int]$pa.G - [int]$pb.G)
    $sum += [Math]::Abs([int]$pa.B - [int]$pb.B)
  }
}
$bmpA.Dispose(); $bmpB.Dispose()
$mean = $sum / ($SW * $SH * 3)
Write-Output ([Math]::Round($mean, 3))
