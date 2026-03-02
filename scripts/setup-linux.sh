#!/usr/bin/env bash
# ============================================================
# NapCat Docker 配置脚本 (Linux)
# 拉取 NapCat 镜像，交互式生成 docker-compose.yml
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.." 

# 检测是否需要 sudo 运行 docker
if docker info &>/dev/null 2>&1; then
    DOCKER_CMD="docker"
else
    echo "⚠️  当前用户无 docker 权限，将使用 sudo"
    DOCKER_CMD="sudo docker"
fi

echo "=== NapCat Docker 配置 (Linux) ==="
echo ""

# ── 1. 拉取 NapCat Docker 镜像 ──────────────────────────
echo "🐳 拉取 NapCat 镜像 ..."
$DOCKER_CMD pull mlikiowa/napcat-docker:latest
echo "✅ 镜像拉取完成"
echo ""

# ── 2. 交互式配置 docker-compose.yml ─────────────────────
echo "📝 配置 docker-compose.yml"
echo ""

# QQ 号
read -rp "请输入 QQ 号 (用于自动登录，留空则每次扫码): " QQ_ACCOUNT
QQ_ACCOUNT="${QQ_ACCOUNT:-}"

# 设备名称
read -rp "请输入设备名称 (QQ 显示的设备名，默认 MyDevice): " HOSTNAME
HOSTNAME="${HOSTNAME:-MyDevice}"

# UID/GID — Linux 默认取当前用户
DEFAULT_UID="$(id -u)"
DEFAULT_GID="$(id -g)"
read -rp "NAPCAT_UID (默认 $DEFAULT_UID): " NAPCAT_UID
NAPCAT_UID="${NAPCAT_UID:-$DEFAULT_UID}"
read -rp "NAPCAT_GID (默认 $DEFAULT_GID): " NAPCAT_GID
NAPCAT_GID="${NAPCAT_GID:-$DEFAULT_GID}"

# ── 3. 生成 docker-compose.yml ──────────────────────────
if [[ -f docker-compose.yml ]]; then
    read -rp "⚠️  docker-compose.yml 已存在，是否覆盖？(y/N): " OVERWRITE
    if [[ "${OVERWRITE,,}" != "y" ]]; then
        echo "已取消，保留现有配置"
        exit 0
    fi
fi

cat > docker-compose.yml <<EOF
services:
  napcat:
    image: mlikiowa/napcat-docker:latest
    container_name: napcat
    hostname: "${HOSTNAME}"
    restart: always
    environment:
      - ACCOUNT=${QQ_ACCOUNT}
      - NAPCAT_UID=\${NAPCAT_UID:-${NAPCAT_UID}}
      - NAPCAT_GID=\${NAPCAT_GID:-${NAPCAT_GID}}
    ports:
      - "3000:3000"   # OneBot HTTP API
      - "3001:3001"   # OneBot WebSocket
      - "6099:6099"   # NapCat WebUI
    volumes:
      - ./napcat/config:/app/napcat/config       # NapCat 配置持久化
      - ./napcat/qq-data:/app/.config/QQ         # QQ 登录态持久化
EOF

echo ""
echo "✅ docker-compose.yml 已生成"
echo ""

# ── 4. 创建数据目录 ─────────────────────────────────────
mkdir -p napcat/config napcat/qq-data
echo "✅ napcat/config 和 napcat/qq-data 目录已就绪"

# ── 5. 生成 OneBot11 接口配置 ────────────────────────────
# 如果指定了 QQ 号，生成对应的 onebot11 配置文件（启用 HTTP + WS）
if [[ -n "$QQ_ACCOUNT" ]]; then
    ONEBOT_CONF="napcat/config/onebot11_${QQ_ACCOUNT}.json"
    if [[ -f "$ONEBOT_CONF" ]]; then
        echo "✅ $ONEBOT_CONF 已存在，跳过"
    else
        cat > "$ONEBOT_CONF" <<OBEOF
{
  "network": {
    "httpServers": [
      {
        "name": "http",
        "enable": true,
        "host": "0.0.0.0",
        "port": 3000,
        "token": ""
      }
    ],
    "httpSseServers": [],
    "httpClients": [],
    "websocketServers": [
      {
        "name": "ws",
        "enable": true,
        "host": "0.0.0.0",
        "port": 3001,
        "token": ""
      }
    ],
    "websocketClients": [],
    "plugins": []
  },
  "musicSignUrl": "",
  "enableLocalFile2Url": false,
  "parseMultMsg": false,
  "imageDownloadProxy": ""
}
OBEOF
        echo "✅ OneBot11 接口配置已生成: $ONEBOT_CONF"
        echo "   HTTP API: 0.0.0.0:3000"
        echo "   WebSocket: 0.0.0.0:3001"
    fi
else
    echo "⚠️  未设置 QQ 号，跳过 OneBot11 配置生成"
    echo "   首次登录后需手动配置 napcat/config/onebot11_<QQ号>.json"
fi

# ── 6. 生成 MCP 客户端配置文件 ───────────────────────────
MCP_CONF="mcp.json"
if [[ -n "$QQ_ACCOUNT" ]]; then
    PROJECT_ABS="$(pwd)"
    UV_PATH="$(command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv")"
    cat > "$MCP_CONF" <<MCPEOF
{
  "mcpServers": {
    "qq-agent": {
      "command": "${UV_PATH}",
      "args": "run --directory ${PROJECT_ABS} qq-agent-mcp --qq ${QQ_ACCOUNT}"
    }
  }
}
MCPEOF
    echo "✅ MCP 客户端配置已生成: $MCP_CONF"
    echo "   默认监听所有群，如需指定可在 args 中添加 \"--groups\", \"群号1,群号2\""
else
    echo "⚠️  未设置 QQ 号，跳过 MCP 配置生成"
fi

echo ""
echo "=== 配置完成 ==="
echo ""
echo "当前配置："
echo "  QQ 号:    ${QQ_ACCOUNT:-（未设置，需扫码登录）}"
echo "  设备名称: ${HOSTNAME}"
echo "  UID/GID:  ${NAPCAT_UID}/${NAPCAT_GID}"
echo ""
echo "下一步: 运行 scripts/start-docker-linux.sh 启动 NapCat"
