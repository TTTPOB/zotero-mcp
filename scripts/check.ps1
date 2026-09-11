$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$requirementsPath = [IO.Path]::GetTempFileName()
Push-Location $projectRoot
try {
    uv export --locked --all-groups --no-emit-project --no-hashes --format requirements-txt --output-file $requirementsPath | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Dependency export failed.' }
    uv run --no-project --with-requirements $requirementsPath python scripts/check.py
    if ($LASTEXITCODE -ne 0) { throw 'Checks failed.' }
}
finally {
    Remove-Item -LiteralPath $requirementsPath -Force -ErrorAction SilentlyContinue
    Pop-Location
}
