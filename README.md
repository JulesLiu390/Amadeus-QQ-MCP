# Amadeus-QQ-MCP

MCP Server，通过 NapCatQQ (OneBot v11) 让 AI 客户端收发 QQ 消息。支持群聊和私聊。

## 功能

- **5 个 MCP 工具**：`check_status`、`get_group_list`、`get_recent_context`、`send_message`、`compress_context`
- WebSocket 实时消息监听 + 自动重连
- 消息按自然语义分段发送（句号/逗号/破折号等），模拟真人打字节奏
- 群/好友白名单控制
- 发送速率限制（3s/目标）

## 前置条件

- Docker
- Python 3.11+
- [uv](https://github.com/astral-sh/uv)

---

## 运行

### 0. 安装 Docker 和 NapCat

**安装 Docker：**

```bash
# macOS — 安装 Docker Desktop
brew install --cask docker

# Ubuntu
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # 免 sudo 运行 docker
# 重新登录终端生效
```

**安装 NapCat：**

本项目使用 [NapCatQQ](https://github.com/NapNeko/NapCatQQ) 的 Docker 镜像作为 QQ 协议端。项目的 `docker-compose.yml` 已配置好镜像，首次运行 `docker compose up -d` 时会自动拉取，无需手动安装。

### 1. 启动 NapCat

```bash
docker compose up -d
```

首次启动需扫码登录，查看二维码：

```bash
docker compose logs -f napcat
```

> **Ubuntu 服务器**：需设置 UID/GID 匹配宿主用户
> ```bash
> NAPCAT_UID=1000 NAPCAT_GID=1000 docker compose up -d
> ```
> macOS 默认值 (501:20) 无需设置。

登录后建议在 `napcat/config/webui.json` 中设置 `"autoLoginAccount": "你的QQ号"`，避免重启后重新扫码。

### 2. 安装依赖 & 启动 MCP Server

```bash
# 安装依赖
uv sync

# 启动（最小参数）
uv run qq-agent-mcp --qq 你的QQ号

# 指定监听群和好友
uv run qq-agent-mcp --qq 你的QQ号 --groups 群号1,群号2 --friends 好友QQ1,好友QQ2

# 全部参数
uv run qq-agent-mcp --qq 你的QQ号 \
  --napcat-host 127.0.0.1 \
  --napcat-port 3000 \
  --ws-port 3001 \
  --groups 群号1,群号2 \
  --friends 好友QQ1 \
  --buffer-size 100 \
  --log-level info
```

### 3. 配置 MCP 客户端

在 AI 客户端（PetGPT、Claude Desktop 等）中添加：

```json
{
  "name": "QQ Agent",
  "transport": "stdio",
  "command": "uv",
  "args": [
    "run", "--directory", "/path/to/qq-mcp",
    "qq-agent-mcp",
    "--qq", "你的QQ号",
    "--groups", "群号1,群号2"
  ]
}
```

或全局安装后直接用：

```bash
uv tool install /path/to/qq-mcp
```

```json
{
  "name": "QQ Agent",
  "transport": "stdio",
  "command": "qq-agent-mcp",
  "args": ["--qq", "你的QQ号", "--groups", "群号1,群号2"]
}
```

---

## CLI 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--qq` (必填) | — | 机器人 QQ 号 |
| `--napcat-host` | `127.0.0.1` | NapCat HTTP 地址 |
| `--napcat-port` | `3000` | NapCat HTTP 端口 |
| `--ws-port` | `3001` | NapCat WebSocket 端口 |
| `--groups` | 全部 | 监听的群号（逗号分隔） |
| `--friends` | 无 | 监听的好友 QQ（逗号分隔） |
| `--buffer-size` | `100` | 每个目标的消息缓冲区大小 |
| `--log-level` | `info` | 日志级别 |

## MCP 工具

| 工具 | 说明 |
|------|------|
| `check_status` | 检查 QQ 登录状态、在线状态、缓冲区统计 |
| `get_group_list` | 获取已加入的群列表 |
| `get_recent_context(target, target_type?, limit?)` | 获取消息上下文（JSON 格式，含 is_self/is_at_me 标记） |
| `send_message(target, content, target_type?, reply_to?)` | 发消息，自动分段+打字延迟 |
| `compress_context(target, target_type?)` | 手动压缩历史消息为摘要 |

## 架构

```
MCP Client (stdio)
  ↕ JSON-RPC
qq-agent-mcp (Python)
  ├── HTTP API → NapCat (OneBot v11) → QQ
  └── WebSocket ← NapCat (消息事件)
       ↓
  Message Buffer (滑动窗口)
```

## 目录结构

```
qq-mcp/
├── src/qq_agent_mcp/    # MCP Server 源码
├── napcat/              # NapCat Docker 挂载目录（见 napcat/README.md）
├── docker-compose.yml   # NapCat 容器配置
├── pyproject.toml       # Python 项目配置
└── test_mcp.py          # 集成测试
```

## License

MIT
# Amadeus-QQ-MCP
