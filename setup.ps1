param(
    [string]$PscadHome,
    [string]$AutomationWheel
)

$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'

Push-Location -LiteralPath $repoRoot
try {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $launcher = Get-Command py -ErrorAction SilentlyContinue
        if ($launcher) {
            & $launcher.Source -3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 or later is required"'
            if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11 or later before running setup.' }
            & $launcher.Source -3 -m venv (Join-Path $repoRoot '.venv')
        } else {
            $launcher = Get-Command python -ErrorAction Stop
            & $launcher.Source -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 or later is required"'
            if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11 or later before running setup.' }
            & $launcher.Source -m venv (Join-Path $repoRoot '.venv')
        }
        if ($LASTEXITCODE -ne 0) { throw 'Creating the independent Python environment failed.' }
    }

    & $venvPython -m pip install -e '.[mcp]'
    if ($LASTEXITCODE -ne 0) { throw 'Installing project dependencies failed; read the pip error above.' }

    $setupArgs = @('-m', 'pscad_driver.setup', '--repo', $repoRoot)
    if ($PscadHome) { $setupArgs += @('--pscad-home', $PscadHome) }
    if ($AutomationWheel) { $setupArgs += @('--automation-wheel', $AutomationWheel) }
    & $venvPython @setupArgs
    if ($LASTEXITCODE -ne 0) { throw 'Setup stopped; completed local steps are retained. Read the error above.' }

    Write-Host 'Ready for use from this checkout. No PSCAD simulation has been started.'
    Write-Host 'CLI: .\.venv\Scripts\pscad-ai.exe --help'
    Write-Host 'MCP snippet: mcp.local.json (import it in your AI client if needed)'
} finally {
    Pop-Location
}
