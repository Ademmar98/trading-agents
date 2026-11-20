# Start the 5K demo #2 deployment (idempotent).
#   1. fill .env      2. python preflight.py      3. run this
$ErrorActionPreference = "SilentlyContinue"
$D = "C:\Users\DELL\OneDrive\1m\propr-bot-5k2"
$py = "C:\Python314\pythonw.exe"

$alive = @(Get-CimInstance Win32_Process -Filter "name='pythonw.exe' OR name='python.exe'") |
    Where-Object { $_.CommandLine -like "*$D\watchdog.py*" }
if ($alive) {
    "watchdog already running (pid $($alive[0].ProcessId))"
    exit 0
}

Push-Location $D
python preflight.py
$rc = $LASTEXITCODE
Pop-Location
if ($rc -ne 0) {
    "preflight failed - not starting"
    exit $rc
}

Start-Process $py -ArgumentList "-u `"$D\watchdog.py`"" -WindowStyle Hidden
Start-Sleep -Seconds 10
Get-CimInstance Win32_Process -Filter "name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like "*propr-bot-5k2*" } |
    ForEach-Object { "pid $($_.ProcessId): $($_.CommandLine)" }
