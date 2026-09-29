param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$OutputDirectory = (Join-Path $ProjectRoot 'outputs\migration')
)

$ErrorActionPreference = 'Stop'
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$archive = Join-Path $OutputDirectory "vista-travel-macos-handoff-$timestamp.zip"
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null

$relativePaths = @(
    'README.md',
    'pyproject.toml',
    'src',
    'tests',
    'skills\pd-vista\task-007-travel-planning',
    'experiments\formal\run_travel_planning_vista_group.py',
    'experiments\formal\vista_macos_preflight.py',
    'experiments\formal\update_opencode_global_codexzh.py',
    'vendor\skillsbench\tasks\travel-planning',
    'migration\macos',
    'data\raw\formal-vista-v65r2-travel-planning-gpt56terra-20260819',
    'data\raw\formal-vista-v65r3-travel-planning-gpt56terra-20260819',
    'data\raw\formal-vista-v65r4-travel-planning-gpt56terra-20260819',
    'outputs\formal_trial_detail_20260819_travel_diagnostics\formal_trial_detail_with_travel_diagnostics.xlsx'
)

$missing = @($relativePaths | Where-Object { -not (Test-Path -LiteralPath (Join-Path $ProjectRoot $_)) })
if ($missing.Count -gt 0) {
    throw "Required handoff paths are missing: $($missing -join ', ')"
}

Push-Location $ProjectRoot
try {
    & tar.exe -a -c -f $archive `
        --exclude='*/__pycache__/*' `
        --exclude='*.pyc' `
        --exclude='*/.pytest_cache/*' `
        --exclude='*/.DS_Store' `
        @relativePaths
    if ($LASTEXITCODE -ne 0) {
        throw "tar.exe failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

$digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
$record = [ordered]@{
    archive = $archive
    sha256 = $digest
    created_at = (Get-Date).ToString('o')
    secrets_included = $false
    docker_assets_included = $false
    full_data_raw_included = $false
}
$recordPath = "$archive.sha256.json"
$record | ConvertTo-Json | Set-Content -LiteralPath $recordPath -Encoding utf8
$record | ConvertTo-Json
