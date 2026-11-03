param([int]$PerLanguage = 150)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    $pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Create .venv and install requirements.lock.txt first; see README.' }
    & $pythonExe scripts/init_env.py
    if ($LASTEXITCODE -ne 0) { throw 'Environment setup failed.' }
    & $pythonExe -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
    docker compose up -d
    if ($LASTEXITCODE -ne 0) { throw 'Neo4j container failed to start.' }
    foreach ($stage in @('collect', 'extract', 'report', 'import-neo4j')) {
        & $pythonExe -u -m player_graph.cli $stage --per-language $PerLanguage
        if ($LASTEXITCODE -ne 0) { throw "Stage failed: $stage" }
    }
} finally {
    Pop-Location
}
