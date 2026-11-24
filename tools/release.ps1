<#
.SYNOPSIS
  Publish a new version: sets the version, commits, tags and pushes. GitHub Actions then
  builds the setup file and creates the release; installed apps offer the update.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\release.ps1 -Version 1.3.0

  Before running: add a "## [1.3.0]" section to CHANGELOG.md describing the changes.
#>
param([Parameter(Mandatory = $true)][string]$Version)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Select-String -Path CHANGELOG.md -Pattern "^## \[$([regex]::Escape($Version))\]" -Quiet)) {
    throw "CHANGELOG.md has no '## [$Version]' section. Write what's new first; it becomes the update notes."
}
$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
& $py tools\set_version.py $Version; if ($LASTEXITCODE) { throw "set_version failed" }
git add -A
git commit -m "Release $Version"
git tag -a "v$Version" -m "SENSIQ $Version"
git push origin HEAD
git push origin "v$Version"
Write-Host "`nPushed v$Version. GitHub is building the release; follow it under the repository's Actions tab." -ForegroundColor Green
