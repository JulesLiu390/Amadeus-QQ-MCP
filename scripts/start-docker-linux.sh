#!/usr/bin/env bash
# ============================================================
# 启动 NapCat Docker 容器 (Linux)
# 首次启动需扫码登录，之后会自动重连
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

# Linux 默认 UID/GID
NAPCAT_UID="${NAPCAT_UID:-$(id -u)}"
NAPCAT_GID="${NAPCAT_GID:-$(id -g)}"

echo "🐳 启动 NapCat (UID=$NAPCAT_UID, GID=$NAPCAT_GID) ..."
NAPCAT_UID="$NAPCAT_UID" NAPCAT_GID="$NAPCAT_GID" $DOCKER_CMD compose up -d

echo ""
echo "✅ NapCat 已启动"
echo ""
echo "首次启动需扫码登录，运行以下命令查看二维码："
echo "  $DOCKER_CMD compose logs -f napcat"
echo ""
echo "WebUI 地址: http://localhost:6099"
