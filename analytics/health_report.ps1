$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repo ".venv\Scripts\python.exe"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$twdata = (& $py -c "import common; print(common.TWDATA)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $twdata) { throw "could not resolve TWDATA" }
$reports = Join-Path $twdata "reports\health"
New-Item -ItemType Directory -Force -Path $reports | Out-Null
$extract = Join-Path $env:TEMP "health_extract_$stamp.json"
& $py (Join-Path $repo "analytics\health_extract.py") --days 7 --out $extract
if ($LASTEXITCODE -ne 0) { throw "health_extract failed with exit $LASTEXITCODE" }
$prompt = (Get-Content (Join-Path $repo "analytics\health_report_prompt.md") -Raw)
$prompt = $prompt.Replace("{EXTRACT_PATH}", $extract)
$agent = Join-Path $env:USERPROFILE ".local\bin\agent.exe"
if (-not (Test-Path $agent)) { $agent = "agent" }
$out = Join-Path $reports "health_$stamp.md"
Set-Location $repo
& $agent -p --output-format text $prompt | Out-File -FilePath $out -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw "agent failed with exit $LASTEXITCODE" }
Remove-Item $extract -ErrorAction SilentlyContinue
Write-Output "report -> $out"
