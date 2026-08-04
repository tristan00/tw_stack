











param([Parameter(Mandatory=$true)][string]$PathA, [Parameter(Mandatory=$true)][string]$PathB,
      [double]$FL = 0.0, [double]$FT = 0.0, [double]$FR = 1.0, [double]$FB = 1.0)
Add-Type -AssemblyName System.Drawing
$SW = 48; $SH = 27
function Get-Small([string]$Path) {
  $img = [System.Drawing.Image]::FromFile($Path)

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
  Write-Output "-1"
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
