Add-Type -AssemblyName System.Drawing
function Crop {
  param([string]$src,[int]$x,[int]$y,[int]$w,[int]$h,[int]$scale,[string]$out)
  $img=[System.Drawing.Image]::FromFile($src)
  $nw=[int]($w*$scale); $nh=[int]($h*$scale)
  $bmp=New-Object System.Drawing.Bitmap -ArgumentList $nw,$nh
  $g=[System.Drawing.Graphics]::FromImage($bmp)
  $g.InterpolationMode=[System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
  $g.PixelOffsetMode=[System.Drawing.Drawing2D.PixelOffsetMode]::Half
  $dst=New-Object System.Drawing.Rectangle -ArgumentList 0,0,$nw,$nh
  $srcR=New-Object System.Drawing.Rectangle -ArgumentList $x,$y,$w,$h
  $g.DrawImage($img,$dst,$srcR,[System.Drawing.GraphicsUnit]::Pixel)
  $bmp.Save($out,[System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose();$bmp.Dispose();$img.Dispose()
  Write-Output "wrote $out ($nw x $nh)"
}
$shots='C:\Users\trist\.claude\jobs\aaf524a7\tmp\shots'
$crops='C:\Users\trist\.claude\jobs\aaf524a7\tmp\crops'
Crop -src "$shots\tab_training.png" -x 32 -y 940 -w 780 -h 250 -scale 3 -out "$crops\train_hist_left.png"
Crop -src "$shots\tab_training.png" -x 580 -y 165 -w 500 -h 300 -scale 3 -out "$crops\train_mid.png"
