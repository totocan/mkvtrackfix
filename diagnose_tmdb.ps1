# tmdb cache diagnose - PowerShell script
# Usage: right-click -> Run with PowerShell, or: .\diagnose_tmdb.ps1

Write-Host "============================================================"
Write-Host " tmdb cache diagnose (PowerShell)"
Write-Host "============================================================"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

# find db
$Db = $null
$candidates = @(
    Join-Path $ScriptDir "cache\tmdb_cache.db",
    Join-Path $env:USERPROFILE "Documents\tmdb_agent\cache\tmdb_cache.db",
    Join-Path $env:USERPROFILE "Documents\tmdb\cache\tmdb_cache.db"
)
foreach ($c in $candidates) {
    if (Test-Path $c) { $Db = $c; break }
}

Write-Host "DB: $Db"
if (-not $Db -or -not (Test-Path $Db)) {
    Write-Host "!!! tmdb_cache.db not found !!!"
    Write-Host "Put this ps1 next to tmdb_manager.py, or pass db path:"
    Write-Host "  .\diagnose_tmdb.ps1 'C:\path\to\tmdb_cache.db'"
    Read-Host "Press Enter to exit"
    exit 1
}

if ($args.Count -ge 1) { $Db = $args[0] }
Write-Host "DB size: $([math]::Round((Get-Item $Db).Length / 1MB, 1)) MB"
Write-Host ""

$Py = $null
if (Get-Command python -ErrorAction SilentlyContinue) { $Py = "python" }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $Py = "py" }

if (-not $Py) {
    Write-Host "!!! python not found. Install Python 3.11 !!!"
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "Python: $Py"
& $Py --version
Write-Host ""

$Out = Join-Path $ScriptDir "diag.txt"
Write-Host "Running diagnose (old db may take minutes to build index)..."
Write-Host ""

& $Py (Join-Path $ScriptDir "diagnose_tmdb.py") $Db 2>&1 | Tee-Object -FilePath $Out

Write-Host ""
Write-Host "============================================================"
Write-Host "Done. Result saved to: $Out"
Write-Host "============================================================"
Read-Host "Press Enter to exit"
