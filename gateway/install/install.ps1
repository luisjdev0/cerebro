# Installs the `cerebro` CLI binary for Windows (amd64) from GitHub Releases.
#
# Served by this deployment's gateway at /install/install.ps1 (static file, see
# gateway/Caddyfile) -- meant to be run as:
#
#   irm https://<your-gateway>/install/install.ps1 | iex
#
# Only downloads and installs the binary -- it does NOT know this deployment's
# public URL or any token. After installing, run `cerebro login --token <your
# admin token> --url <this gateway's URL>` yourself (see
# luisjdev-pendientes/ecosistema-cerebro for why: a static script has no
# reliable way to know the URL it was fetched through, and `cerebro login`
# already does exactly this, tested and working).

$ErrorActionPreference = "Stop"

$CerebroRepo = if ($env:CEREBRO_REPO) { $env:CEREBRO_REPO } else { "luisjdev0/cerebro" }
$InstallDir = if ($env:CEREBRO_INSTALL_DIR) { $env:CEREBRO_INSTALL_DIR } else { "$env:USERPROFILE\.cerebro\bin" }
$AssetName = "cerebro-windows-amd64.exe"
$Url = "https://github.com/$CerebroRepo/releases/latest/download/$AssetName"
$DestFile = Join-Path $InstallDir "cerebro.exe"

Write-Host "Descargando $Url..."
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Invoke-WebRequest -Uri $Url -OutFile $DestFile

Write-Host "cerebro instalado en $DestFile"

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$InstallDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$InstallDir", "User")
    Write-Host ""
    Write-Host "Se agrego $InstallDir a tu PATH de usuario. Abre una terminal nueva para que tome efecto."
}

Write-Host ""
Write-Host "Siguiente paso: cerebro login --token <tu-token> --url <url-de-tu-gateway>"
