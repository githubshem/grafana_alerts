# Run a provisioning script with a Python that has dependencies installed.
# Usage: .\run.ps1 apply_rules.py --dry-run --output output/compiled_rules.json
$ErrorActionPreference = "Stop"
if (-not $args -or $args.Count -lt 1) {
    throw "Usage: .\run.ps1 <script.py> [script args...]"
}

$Root = $PSScriptRoot
$Requirements = Join-Path $Root "requirements.txt"
$ScriptPath = Join-Path (Join-Path $Root "scripts") $args[0]
$ScriptArgs = @()
if ($args.Count -gt 1) {
    $ScriptArgs = $args[1..($args.Count - 1)]
}

if (-not (Test-Path $ScriptPath)) {
    throw "Script not found: $ScriptPath"
}

$PythonExe = $null
$PythonLauncherArg = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonExe = "py"
    $PythonLauncherArg = "-3.13"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonExe = "python"
} else {
    throw "No suitable Python found. Install Python 3.13 or ensure 'py -3.13' works."
}

function Invoke-Python {
    param([string[]]$PythonArgs)
    if ($PythonLauncherArg) {
        & $PythonExe $PythonLauncherArg @PythonArgs
    } else {
        & $PythonExe @PythonArgs
    }
}

Invoke-Python @("-m", "pip", "install", "-r", $Requirements, "-q")
if ($PythonLauncherArg) {
    & $PythonExe $PythonLauncherArg $ScriptPath @ScriptArgs
} else {
    & $PythonExe $ScriptPath @ScriptArgs
}
exit $LASTEXITCODE
