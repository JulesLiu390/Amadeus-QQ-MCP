# ============================================================
# NapCat Docker 配置脚本 (Windows)
# 拉取 NapCat 镜像，交互式生成 docker-compose.yml
# ============================================================
$ErrorActionPreference = "Continue"

$ProjectDir = Split-Path $PSScriptRoot -Parent
Push-Location $ProjectDir

# ── 0. 检查 Docker 是否可用 ────────────────────────────────
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
Write-Host "Docker 已就绪" -ForegroundColor Green
Write-Host ""
Write-Host "=== NapCat Docker 配置 (Windows) ===" -ForegroundColor Cyan
Write-Host ""

# ── 1. 拉取 NapCat Docker 镜像 ────────────────────────────
Write-Host "拉取 NapCat 镜像 ..." -ForegroundColor Cyan
& docker pull mlikiowa/napcat-docker:latest
Write-Host "镜像拉取完成" -ForegroundColor Green
Write-Host ""

# ── 2. 交互式配置 ─────────────────────────────────────────
Write-Host "配置 docker-compose.yml" -ForegroundColor Cyan
Write-Host ""

$QQ_ACCOUNT = Read-Host "请输入 QQ 号 (用于自动登录，留空则每次扫码)"
$HOSTNAME_VAL = Read-Host "请输入设备名称 (QQ 显示的设备名，默认 MyDevice)"
if ([string]::IsNullOrWhiteSpace($HOSTNAME_VAL)) {
    $HOSTNAME_VAL = "MyDevice"
}

# ── 3. 生成 docker-compose.yml ─────────────────────────────
if (Test-Path "docker-compose.yml") {
    $overwrite = Read-Host "docker-compose.yml 已存在，是否覆盖？(y/N)"
    if ($overwrite -ne "y") {
        Write-Host "已取消，保留现有配置"
        Pop-Location; exit 0
    }
}

$lines = @(
    "services:",
    "  napcat:",
    "    image: mlikiowa/napcat-docker:latest",
    "    container_name: napcat",
    "    hostname: `"$HOSTNAME_VAL`"",
    "    restart: always",
    "    environment:",
    "      - ACCOUNT=$QQ_ACCOUNT",
    "    ports:",
    "      - `"3000:3000`"   # OneBot HTTP API",
    "      - `"3001:3001`"   # OneBot WebSocket",
    "      - `"6099:6099`"   # NapCat WebUI",
    "    volumes:",
    "      - ./napcat/config:/app/napcat/config       # NapCat 配置持久化",
    "      - ./napcat/qq-data:/app/.config/QQ         # QQ 登录态持久化"
)
$lines -join "`n" | Set-Content -Path "docker-compose.yml" -Encoding UTF8 -NoNewline

Write-Host ""
Write-Host "docker-compose.yml 已生成" -ForegroundColor Green
Write-Host ""

# ── 4. 创建数据目录 ────────────────────────────────────────
New-Item -ItemType Directory -Path "napcat\config" -Force | Out-Null
New-Item -ItemType Directory -Path "napcat\qq-data" -Force | Out-Null
Write-Host "napcat\config 和 napcat\qq-data 目录已就绪" -ForegroundColor Green

# ── 5. 生成 OneBot11 接口配置 ──────────────────────────────
if (-not [string]::IsNullOrWhiteSpace($QQ_ACCOUNT)) {
    $onebotConf = "napcat\config\onebot11_${QQ_ACCOUNT}.json"
    if (Test-Path $onebotConf) {
        Write-Host "$onebotConf 已存在，跳过" -ForegroundColor Green
    }
    else {
        $json = @(
            "{",
            "  `"network`": {",
            "    `"httpServers`": [",
            "      {",
            "        `"name`": `"http`",",
            "        `"enable`": true,",
            "        `"host`": `"0.0.0.0`",",
            "        `"port`": 3000,",
            "        `"token`": `"`"",
            "      }",
            "    ],",
            "    `"httpSseServers`": [],",
            "    `"httpClients`": [],",
            "    `"websocketServers`": [",
            "      {",
            "        `"name`": `"ws`",",
            "        `"enable`": true,",
            "        `"host`": `"0.0.0.0`",",
            "        `"port`": 3001,",
            "        `"token`": `"`"",
            "      }",
            "    ],",
            "    `"websocketClients`": [],",
            "    `"plugins`": []",
            "  },",
            "  `"musicSignUrl`": `"`",",
            "  `"enableLocalFile2Url`": false,",
            "  `"parseMultMsg`": false,",
            "  `"imageDownloadProxy`": `"`"",
            "}"
        )
        $json -join "`n" | Set-Content -Path $onebotConf -Encoding UTF8 -NoNewline
        Write-Host "OneBot11 接口配置已生成: $onebotConf" -ForegroundColor Green
        Write-Host "   HTTP API: 0.0.0.0:3000"
        Write-Host "   WebSocket: 0.0.0.0:3001"
    }
}
else {
    Write-Host "未设置 QQ 号，跳过 OneBot11 配置生成" -ForegroundColor Yellow
}

# ── 6. 生成 MCP 客户端配置文件 ─────────────────────────────
if (-not [string]::IsNullOrWhiteSpace($QQ_ACCOUNT)) {
    $projectAbs = (Get-Location).Path -replace "\\", "/"
    $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCmd) {
        $uvPath = $uvCmd.Source -replace "\\", "/"
    }
    else {
        $uvPath = "uv"
    }

    $mcp = @(
        "{",
        "  `"mcpServers`": {",
        "    `"qq-agent`": {",
        "      `"command`": `"$uvPath`",",
        "      `"args`": `"run --directory $projectAbs qq-agent-mcp --qq $QQ_ACCOUNT`"",
        "    }",
        "  }",
        "}"
    )
    $mcp -join "`n" | Set-Content -Path "mcp.json" -Encoding UTF8 -NoNewline
    Write-Host "MCP 客户端配置已生成: mcp.json" -ForegroundColor Green
}
else {
    Write-Host "未设置 QQ 号，跳过 MCP 配置生成" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== 配置完成 ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "当前配置："
if ([string]::IsNullOrWhiteSpace($QQ_ACCOUNT)) {
    Write-Host "  QQ 号:    （未设置，需扫码登录）"
}
else {
    Write-Host "  QQ 号:    $QQ_ACCOUNT"
}
Write-Host "  设备名称: $HOSTNAME_VAL"
Write-Host ""
Write-Host "下一步: 运行 scripts\start-docker-windows.ps1 启动 NapCat" -ForegroundColor Cyan

Pop-Location
