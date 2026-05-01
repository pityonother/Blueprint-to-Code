$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Drawing

$root = "C:\Users\ac\Documents\project gaming"
$assetsRoot = Join-Path $root "public\assets"
$bgDir = Join-Path $assetsRoot "bg"
$spritesDir = Join-Path $assetsRoot "sprites"
$uiDir = Join-Path $assetsRoot "ui"
$audioDir = Join-Path $assetsRoot "audio"

New-Item -ItemType Directory -Force -Path $bgDir, $spritesDir, $uiDir, $audioDir | Out-Null

function Save-Bitmap {
  param(
    [string]$Path,
    [System.Drawing.Bitmap]$Bitmap
  )

  $dir = Split-Path -Parent $Path
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $Bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
  $Bitmap.Dispose()
}

function New-Graphics {
  param([System.Drawing.Bitmap]$Bitmap)
  $g = [System.Drawing.Graphics]::FromImage($Bitmap)
  $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
  return $g
}

function Fill-RoundedRect {
  param(
    [System.Drawing.Graphics]$Graphics,
    [System.Drawing.Brush]$Brush,
    [float]$X,
    [float]$Y,
    [float]$Width,
    [float]$Height,
    [float]$Radius
  )

  $path = New-Object System.Drawing.Drawing2D.GraphicsPath
  $d = $Radius * 2
  $path.AddArc($X, $Y, $d, $d, 180, 90)
  $path.AddArc($X + $Width - $d, $Y, $d, $d, 270, 90)
  $path.AddArc($X + $Width - $d, $Y + $Height - $d, $d, $d, 0, 90)
  $path.AddArc($X, $Y + $Height - $d, $d, $d, 90, 90)
  $path.CloseFigure()
  $Graphics.FillPath($Brush, $path)
  $path.Dispose()
}

function New-RoundedRectPath {
  param(
    [float]$X,
    [float]$Y,
    [float]$Width,
    [float]$Height,
    [float]$Radius
  )

  $path = New-Object System.Drawing.Drawing2D.GraphicsPath
  $d = $Radius * 2
  $path.AddArc($X, $Y, $d, $d, 180, 90)
  $path.AddArc($X + $Width - $d, $Y, $d, $d, 270, 90)
  $path.AddArc($X + $Width - $d, $Y + $Height - $d, $d, $d, 0, 90)
  $path.AddArc($X, $Y + $Height - $d, $d, $d, 90, 90)
  $path.CloseFigure()
  return $path
}

function Draw-Sky {
  $bmp = New-Object System.Drawing.Bitmap 1280, 720
  $g = New-Graphics $bmp
  $rect = New-Object System.Drawing.Rectangle 0, 0, 1280, 720
  $brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush $rect, ([System.Drawing.Color]::FromArgb(255,255,247,232)), ([System.Drawing.Color]::FromArgb(255,233,129,58)), 90
  $blend = New-Object System.Drawing.Drawing2D.ColorBlend
  $blend.Colors = @(
    [System.Drawing.Color]::FromArgb(255,255,247,232),
    [System.Drawing.Color]::FromArgb(255,248,215,157),
    [System.Drawing.Color]::FromArgb(255,233,129,58)
  )
  $blend.Positions = @(0.0, 0.5, 1.0)
  $brush.InterpolationColors = $blend
  $g.FillRectangle($brush, $rect)

  $sunBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(120,255,241,214))
  $g.FillEllipse($sunBrush, 890, 85, 230, 230)

  for ($i = 0; $i -lt 10; $i++) {
    $alpha = 18 + ($i * 6)
    $cloudBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb($alpha,255,245,235))
    $x = 120 + ($i * 95)
    $y = 80 + (($i % 3) * 45)
    $g.FillEllipse($cloudBrush, $x, $y, 170, 42)
    $g.FillEllipse($cloudBrush, $x + 35, $y - 16, 130, 48)
    $g.FillEllipse($cloudBrush, $x + 80, $y, 150, 42)
  }

  $sunBrush.Dispose()
  $brush.Dispose()
  $g.Dispose()
  Save-Bitmap (Join-Path $bgDir "sky.png") $bmp
}

function Draw-Layer {
  param(
    [string]$Name,
    [int]$Height,
    [System.Drawing.Color]$BaseColor,
    [System.Drawing.Color]$AccentColor,
    [int]$Count
  )

  $bmp = New-Object System.Drawing.Bitmap 1600, $Height
  $g = New-Graphics $bmp
  $g.Clear([System.Drawing.Color]::Transparent)

  for ($i = 0; $i -lt $Count; $i++) {
    $w = Get-Random -Minimum 60 -Maximum 150
    $h = Get-Random -Minimum ([Math]::Floor($Height * 0.35)) -Maximum ([Math]::Floor($Height * 0.82))
    $x = [int]($i * (1600 / $Count)) + (Get-Random -Minimum -12 -Maximum 24)
    $y = $Height - $h
    $fillColor = if ($i % 2 -eq 0) { $BaseColor } else { $AccentColor }
    $brush = New-Object System.Drawing.SolidBrush $fillColor
    $g.FillRectangle($brush, $x, $y, $w, $h)
    if ($i % 3 -eq 0) {
      $g.FillEllipse($brush, $x - 14, $y - 36, $w + 28, 60)
    }
    if ($i % 4 -eq 0) {
      $windowBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(70,255,216,163))
      for ($row = 0; $row -lt 3; $row++) {
        for ($col = 0; $col -lt 2; $col++) {
          $g.FillRectangle($windowBrush, $x + 14 + ($col * 22), $y + 18 + ($row * 25), 10, 14)
        }
      }
      $windowBrush.Dispose()
    }
    $brush.Dispose()
  }

  $g.Dispose()
  Save-Bitmap (Join-Path $bgDir $Name) $bmp
}

function Draw-Textbox {
  $bmp = New-Object System.Drawing.Bitmap 1024, 160
  $g = New-Graphics $bmp
  $g.Clear([System.Drawing.Color]::Transparent)
  $shadow = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(60,0,0,0))
  Fill-RoundedRect $g $shadow 12 18 1000 126 26
  $panel = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(210,10,8,10))
  Fill-RoundedRect $g $panel 0 0 1000 126 26
  $stroke = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(80,255,247,230)), 2
  $outline = New-RoundedRectPath 1 1 998 124 26
  $g.DrawPath($stroke, $outline)
  $outline.Dispose()
  $g.Dispose()
  Save-Bitmap (Join-Path $uiDir "textbox_bg.png") $bmp
}

function Draw-Vignette {
  $bmp = New-Object System.Drawing.Bitmap 1280, 720
  $g = New-Graphics $bmp
  $path = New-Object System.Drawing.Drawing2D.GraphicsPath
  $path.AddRectangle((New-Object System.Drawing.Rectangle 0,0,1280,720))
  $center = New-Object System.Drawing.Rectangle 160, 70, 960, 580
  $path.AddEllipse($center)
  $brush = New-Object System.Drawing.Drawing2D.PathGradientBrush $path
  $brush.CenterColor = [System.Drawing.Color]::FromArgb(0,0,0,0)
  $brush.SurroundColors = @([System.Drawing.Color]::FromArgb(220,10,6,4))
  $g.FillRectangle((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(25,0,0,0))),0,0,1280,720)
  $g.FillPath($brush, $path)
  $brush.Dispose()
  $path.Dispose()
  $g.Dispose()
  Save-Bitmap (Join-Path $uiDir "vignette.png") $bmp
}

function Draw-Glow {
  $bmp = New-Object System.Drawing.Bitmap 128, 128
  $g = New-Graphics $bmp
  $path = New-Object System.Drawing.Drawing2D.GraphicsPath
  $path.AddEllipse(8,8,112,112)
  $brush = New-Object System.Drawing.Drawing2D.PathGradientBrush $path
  $brush.CenterColor = [System.Drawing.Color]::FromArgb(235,255,247,230)
  $brush.SurroundColors = @([System.Drawing.Color]::FromArgb(0,245,201,122))
  $g.FillPath($brush, $path)
  $brush.Dispose()
  $path.Dispose()
  $g.Dispose()
  Save-Bitmap (Join-Path $uiDir "trigger_glow.png") $bmp
}

function Draw-Petal {
  $bmp = New-Object System.Drawing.Bitmap 24, 24
  $g = New-Graphics $bmp
  $brush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,255,182,193))
  $g.FillEllipse($brush, 5, 2, 12, 18)
  $g.FillEllipse((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(160,255,214,223))), 8, 4, 6, 14)
  $g.Dispose()
  Save-Bitmap (Join-Path $spritesDir "petal.png") $bmp
}

function Draw-CharacterSheet {
  $frameW = 96
  $frameH = 96
  $bmp = New-Object System.Drawing.Bitmap ($frameW * 4), $frameH
  $g = New-Graphics $bmp
  $g.Clear([System.Drawing.Color]::Transparent)
  $bodyBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,60,48,44))
  $coatBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,120,85,68))
  $skinBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,241,217,192))
  $hairBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,50,35,30))

  for ($i = 0; $i -lt 4; $i++) {
    $ox = $i * $frameW
    $legOffset = if ($i % 2 -eq 0) { -4 } else { 4 }
    $armOffset = if ($i % 2 -eq 0) { 4 } else { -3 }
    $g.FillEllipse($hairBrush, $ox + 40, 10, 22, 22)
    $g.FillEllipse($skinBrush, $ox + 42, 16, 18, 18)
    $g.FillPie($hairBrush, $ox + 40, 10, 22, 20, 180, 180)
    $g.FillRectangle($coatBrush, $ox + 38, 34, 24, 34)
    $g.FillRectangle($bodyBrush, $ox + 58 + $armOffset, 37, 7, 26)
    $g.FillRectangle($bodyBrush, $ox + 44 - $legOffset, 66, 7, 24)
    $g.FillRectangle($bodyBrush, $ox + 56 + $legOffset, 66, 7, 24)
    $g.FillRectangle((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,213,155,124))), $ox + 62 + $armOffset, 58, 5, 8)
  }

  $g.Dispose()
  Save-Bitmap (Join-Path $spritesDir "player.png") $bmp
}

function Draw-SimpleSprite {
  param(
    [string]$Name,
    [int]$Width,
    [int]$Height,
    [scriptblock]$Painter
  )

  $bmp = New-Object System.Drawing.Bitmap $Width, $Height
  $g = New-Graphics $bmp
  $g.Clear([System.Drawing.Color]::Transparent)
  & $Painter $g
  $g.Dispose()
  Save-Bitmap (Join-Path $spritesDir $Name) $bmp
}

Draw-Sky
Draw-Layer "far.png" 260 ([System.Drawing.Color]::FromArgb(255,106,93,101)) ([System.Drawing.Color]::FromArgb(255,86,72,83)) 13
Draw-Layer "mid.png" 280 ([System.Drawing.Color]::FromArgb(255,76,59,67)) ([System.Drawing.Color]::FromArgb(255,61,47,56)) 18
Draw-Layer "near.png" 240 ([System.Drawing.Color]::FromArgb(255,48,37,34)) ([System.Drawing.Color]::FromArgb(255,62,45,40)) 26
Draw-Textbox
Draw-Vignette
Draw-Glow
Draw-Petal
Draw-CharacterSheet

Draw-SimpleSprite "tree_cherry.png" 256 320 {
  param($g)
  $g.FillRectangle((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,83,56,43))), 118, 134, 24, 150)
  $g.FillRectangle((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,83,56,43))), 103, 104, 16, 98)
  $g.FillRectangle((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,83,56,43))), 142, 112, 14, 86)
  $g.FillEllipse((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,224,162,186))), 40, 16, 180, 128)
  $g.FillEllipse((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(220,248,196,214))), 62, 30, 148, 104)
}

Draw-SimpleSprite "shop_old.png" 320 256 {
  param($g)
  $g.FillRectangle((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,81,59,47))), 42, 60, 236, 146)
  $g.FillRectangle((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,60,44,37))), 68, 92, 112, 74)
  $g.FillRectangle((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,151,110,84))), 194, 92, 46, 114)
  $g.FillRectangle((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,44,31,25))), 30, 48, 260, 20)
  $g.FillRectangle((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,214,170,118))), 78, 102, 92, 54)
}

Draw-SimpleSprite "bench_cat.png" 220 128 {
  param($g)
  $wood = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,90,67,54))
  $dark = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,41,31,27))
  $g.FillRectangle($wood, 58, 48, 104, 12)
  $g.FillRectangle($wood, 58, 64, 104, 12)
  $g.FillRectangle($dark, 68, 74, 8, 40)
  $g.FillRectangle($dark, 144, 74, 8, 40)
  $g.FillEllipse($dark, 156, 36, 28, 16)
  $g.FillPolygon($dark, @(
    (New-Object System.Drawing.Point 160,42),
    (New-Object System.Drawing.Point 166,30),
    (New-Object System.Drawing.Point 172,42)
  ))
  $g.FillPolygon($dark, @(
    (New-Object System.Drawing.Point 172,42),
    (New-Object System.Drawing.Point 178,30),
    (New-Object System.Drawing.Point 184,42)
  ))
}

Draw-SimpleSprite "sunset_wall.png" 320 180 {
  param($g)
  $wall = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,190,136,98))
  $top = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,92,61,47))
  $light = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(120,255,219,160))
  $g.FillRectangle($wall, 12, 36, 296, 110)
  $g.FillRectangle($top, 0, 18, 320, 24)
  $g.FillEllipse($light, 188, 46, 110, 70)
}

Draw-SimpleSprite "mailbox.png" 128 220 {
  param($g)
  $post = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,69,52,44))
  $box = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,173,84,60))
  $slot = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,54,40,35))
  $g.FillRectangle($post, 56, 64, 18, 130)
  Fill-RoundedRect $g $box 34 22 64 52 10
  $g.FillRectangle($slot, 48, 42, 36, 8)
}

Draw-SimpleSprite "door_home.png" 220 360 {
  param($g)
  $frame = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,74,55,47))
  $door = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,34,25,22))
  $panel = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(110,112,81,66)), 2
  $g.FillRectangle($frame, 22, 18, 176, 320)
  Fill-RoundedRect $g $door 52 46 116 274 8
  $g.DrawRectangle($panel, 68, 70, 84, 98)
  $g.DrawRectangle($panel, 68, 188, 84, 100)
  $g.FillEllipse((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255,241,202,123))), 142, 176, 10, 10)
}

@("ambient.ogg","pad.ogg","piano.ogg") | ForEach-Object {
  $path = Join-Path $audioDir $_
  if (-not (Test-Path $path)) {
    [System.IO.File]::WriteAllBytes($path, [byte[]]@())
  }
}
