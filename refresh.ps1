# Download / refresh SEC Form N-CEN data, load it and rebuild the model.
#   .\refresh.ps1                  full history (2018q3 onwards)
#   .\refresh.ps1 -Since 2022q1    smaller first run
param([string]$Since = "2018q3")
Set-Location $PSScriptRoot
if (-not $env:SEC_USER_AGENT) {
    $env:SEC_USER_AGENT = Read-Host "SEC User-Agent (your name and email, e.g. 'Karthik Rangaraj you@example.com')"
}
& .\.venv\Scripts\python.exe -m custody_intel.cli refresh --since $Since
if ($LASTEXITCODE -eq 0) { & .\.venv\Scripts\python.exe -m custody_intel.cli status }
