#!/usr/bin/env bash
# ============================================================
# Amadeus-QQ-MCP 一键安装脚本 (Linux)
# 安装 Docker、uv，初始化项目配置并安装 Python 依赖
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "=== Amadeus-QQ-MCP 安装 (Linux) ==="
echo ""

# ── 1. 安装 Docker ───────────────────────────────────────
if command -v docker &>/dev/null; then
    echo "✅ Docker 已安装: $(docker --version)"
else
    echo "📦 正在安装 Docker ..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
    echo "✅ Docker 安装完成"
    echo "⚠️  已将当前用户加入 docker 组，需重新登录终端才能免 sudo 使用 docker"
fi

# ── 2. 安装 uv ──────────────────────────────────────────
if command -v uv &>/dev/null; then
    echo "✅ uv 已安装: $(uv --version)"
else
    echo "📦 正在安装 uv ..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # 让当前脚本后续命令能找到 uv
    export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
    # 写入 ~/.bashrc 让新终端也能找到 uv
    UV_PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'
    if ! grep -qF '.local/bin' "$HOME/.bashrc" 2>/dev/null; then
        echo "" >> "$HOME/.bashrc"
        echo "# uv (installed by Amadeus-QQ-MCP)" >> "$HOME/.bashrc"
        echo "$UV_PATH_LINE" >> "$HOME/.bashrc"
        echo "   已将 ~/.local/bin 写入 ~/.bashrc"
    fi
    if command -v uv &>/dev/null; then
        echo "✅ uv 安装完成: $(uv --version)"
    else
        echo "❌ uv 安装后仍无法找到，请手动检查"
        echo "   尝试: source \$HOME/.cargo/env 或重新打开终端"
        exit 1
    fi
    echo ""
    echo "⚠️  uv 刚刚安装，当前终端可能需要执行:"
    echo "     source ~/.bashrc"
    echo "   或打开一个新终端。"
fi

# ── 3. 初始化 docker-compose.yml ─────────────────────────
if [[ ! -f docker-compose.yml ]]; then
    echo ""
    echo "📄 从模板创建 docker-compose.yml ..."
    cp docker-compose.sample.yml docker-compose.yml
    echo "   请编辑 docker-compose.yml 填写你的 QQ 号等配置"
else
    echo "✅ docker-compose.yml 已存在，跳过"
fi

# ── 4. 创建 NapCat 数据目录 ──────────────────────────────
mkdir -p napcat/config napcat/qq-data
echo "✅ napcat/config 和 napcat/qq-data 目录已就绪"

# ── 5. 安装 Python 依赖 ──────────────────────────────────
echo ""
echo "📦 安装 Python 依赖 ..."
uv sync
echo "✅ Python 依赖安装完成"

echo ""
echo "=== 安装完成 ==="
echo ""
echo "后续步骤："
echo "  1. 编辑 docker-compose.yml，设置 ACCOUNT 为你的 QQ 号"
echo "  2. 运行 scripts/start-docker-linux.sh 启动 NapCat 并扫码登录"
echo "  3. 使用 uv run qq-agent-mcp --qq <你的QQ号> 启动 MCP Server"
