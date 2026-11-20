# Auto-start the funding watchdogs. Idempotent: skips any watchdog that is
# already running, so it is safe if it runs more than once per boot.
$ErrorActionPreference = "SilentlyContinue"
$py = "C:\Python314\pythonw.exe"
$targets = @(
    "C:\Users\DELL\OneDrive\1m\propr-bot\watchdog.py",
    "C:\Users\DELL\OneDrive\1m\propr-bot-5k\watchdog.py",
    "C:\Users\DELL\OneDrive\1m\propr-bot-5k2\watchdog.py"
)

$proc = @(Get-CimInstance Win32_Process -Filter "name='pythonw.exe' OR name='python.exe'")
foreach ($t in $targets) {
    $alive = $proc | Where-Object { $_.CommandLine -like "*$t*" }
    if ($alive) { continue }
    Start-Process $py -ArgumentList "-u `"$t`"" -WindowStyle Hidden
    Start-Sleep -Seconds 3
}
