<#
.SYNOPSIS
    Local checks, identical to what CI runs.

.DESCRIPTION
    The Windows twin of the Makefile. Both run the same commands in the same order as the
    CI workflow, so a green run locally means a green run in CI.

.PARAMETER Task
    Which task to run: check (default), lint, typecheck, test, coverage, format, install.

.EXAMPLE
    .\check.ps1
    .\check.ps1 -Task lint
#>
[CmdletBinding()]
param(
    [ValidateSet('check', 'lint', 'typecheck', 'test', 'coverage', 'format', 'install', 'docs-lint')]
    [string]$Task = 'check'
)

$ErrorActionPreference = 'Stop'

function Invoke-Step {
    param([string]$Name, [scriptblock]$Action)

    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) {
        Write-Host "$Name failed with exit code $LASTEXITCODE" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

switch ($Task) {
    'install' {
        Invoke-Step 'create virtual environment' { uv venv --python 3.13 }
        Invoke-Step 'install project' { uv pip install -e ".[dev]" }
    }
    'format' {
        Invoke-Step 'format' { uv run --no-sync ruff format . }
    }
    'lint' {
        Invoke-Step 'format check' { uv run --no-sync ruff format --check . }
        Invoke-Step 'lint' { uv run --no-sync ruff check . }
    }
    'typecheck' {
        Invoke-Step 'typecheck' { uv run --no-sync mypy }
    }
    'test' {
        Invoke-Step 'test' { uv run --no-sync pytest -m "not hardware" }
    }
    'coverage' {
        Invoke-Step 'coverage' { uv run --no-sync pytest -m "not hardware" --cov --cov-report=term-missing }
    }
    'docs-lint' {
        # Needs Node, so it is not part of `check`. CI runs it as its own job.
        Invoke-Step 'markdown lint' { npx --yes markdownlint-cli2 }
    }
    'check' {
        Invoke-Step 'format check' { uv run --no-sync ruff format --check . }
        Invoke-Step 'lint' { uv run --no-sync ruff check . }
        Invoke-Step 'typecheck' { uv run --no-sync mypy }
        Invoke-Step 'test' { uv run --no-sync pytest -m "not hardware" }
        Write-Host 'All checks passed.' -ForegroundColor Green
    }
}

