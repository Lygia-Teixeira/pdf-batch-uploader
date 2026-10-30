<#
.SYNOPSIS
  Gera um executável Windows (onedir) com PyInstaller.

.DESCRIPTION
  Genérico: sem ícone/logo de terceiros, sem dados embutidos. O Chromium do
  Playwright NÃO é embutido por padrão; use -BundleChromium para incluí-lo
  (aumenta o pacote em centenas de MB). Dados do usuário continuam em
  %LOCALAPPDATA%\PdfBatchUploader e nunca são empacotados.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1
  powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1 -BundleChromium
#>
param([switch]$BundleChromium)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

py -3 -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar dependências.' }

$pyArgs = @('-3', '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
          '--name', 'PdfBatchUploader', '--collect-all', 'playwright',
          '--add-data', 'examples;examples')

$browserDir = Join-Path $root '_chromium_bundle'
try {
    if ($BundleChromium) {
        if (Test-Path $browserDir) { Remove-Item $browserDir -Recurse -Force }
        $env:PLAYWRIGHT_BROWSERS_PATH = $browserDir
        py -3 -m playwright install chromium
        if ($LASTEXITCODE -ne 0) { throw 'Falha ao baixar o Chromium.' }
        $pyArgs += @('--add-data', "${browserDir};playwright-browsers")
    }
    $pyArgs += 'scripts\entry_ui.py'
    & py @args
    if ($LASTEXITCODE -ne 0) { throw 'Falha no PyInstaller.' }
    Write-Host "EXE: $root\dist\PdfBatchUploader\PdfBatchUploader.exe" -ForegroundColor Green
    if (-not $BundleChromium) {
        Write-Host 'Chromium não incluído: rode "python -m playwright install chromium" na máquina de destino.' -ForegroundColor Yellow
    }
} finally {
    Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH -ErrorAction SilentlyContinue
}
