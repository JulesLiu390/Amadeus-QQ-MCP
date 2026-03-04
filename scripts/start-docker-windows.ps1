# ============================================================
# 启动 NapCat Docker 容器 (Windows)
# 首次启动需扫码登录，之后会自动重连
# ============================================================
$ErrorActionPreference = "Continue"

$ProjectDir = Split-Path $PSScriptRoot -Parent
Push-Location $ProjectDir

# ── 检查 docker-compose.yml ────────────────────────────────
if (-not (Test-Path "docker-compose.yml")) {
    Write-Host "docker-compose.yml 不存在，请先运行 scripts\setup-windows.ps1" -ForegroundColor Red
    Pop-Location; exit 1
}

# ── 检查 Docker 是否可用 ───────────────────────────────────
$dockerReady = $false
$output = & docker info 2>&1
if ($output -match "Server Version") {
    $dockerReady = $true
}

if (-not $dockerReady) {
    Write-Host "Docker 未就绪，尝试启动 Docker Desktop ..." -ForegroundColor Yellow
    $dockerExe = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $dockerExe)) {
        Write-Host "未找到 Docker Desktop，请先安装" -ForegroundColor Red
        Pop-Location; exit 1
    }
    Start-Process -FilePath $dockerExe
    Write-Host "等待 Docker 引擎启动 ..." -ForegroundColor Yellow
    for ($i = 0; $i -lt 24; $i++) {
        Start-Sleep -Seconds 5
        $output = & docker info 2>&1
        if ($output -match "Server Version") {
            $dockerReady = $true
            break
        }
    }
    if (-not $dockerReady) {
        Write-Host "Docker 引擎启动超时，请手动启动 Docker Desktop 后重试" -ForegroundColor Red
        Pop-Location; exit 1
    }
}

# ── 启动容器 ───────────────────────────────────────────────
Write-Host "启动 NapCat ..." -ForegroundColor Cyan
& docker compose up -d

Write-Host ""
Write-Host "NapCat 已启动" -ForegroundColor Green
Write-Host ""
Write-Host "首次启动需扫码登录，运行以下命令查看日志："
Write-Host "  docker compose logs -f napcat" -ForegroundColor Yellow
Write-Host ""
Write-Host "WebUI 地址: http://localhost:6099" -ForegroundColor Cyan

Pop-Location
