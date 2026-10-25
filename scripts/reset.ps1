$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
docker compose down -v --remove-orphans
if ($LASTEXITCODE -ne 0) { throw 'Compose reset failed' }
$workspacePath = (Get-Location).Path
foreach ($fileName in @('incidents.jsonl', 'incidents.tmp')) {
    $targetPath = [System.IO.Path]::GetFullPath((Join-Path $workspacePath "ground-truth/$fileName"))
    if (-not $targetPath.StartsWith($workspacePath + [System.IO.Path]::DirectorySeparatorChar)) {
        throw 'Reset path is outside the workspace'
    }
    if (Test-Path -LiteralPath $targetPath) { Remove-Item -LiteralPath $targetPath }
}
docker compose up --build -d
if ($LASTEXITCODE -ne 0) { throw 'Compose startup failed' }
