# Amadeus-QQ-MCP

MCP Server，通过 NapCatQQ (OneBot v11) 让 AI 客户端收发 QQ 消息。支持群聊和私聊。

## 功能

- **6 个 MCP 工具**：`check_status`、`get_group_list`、`get_recent_context`、`batch_get_recent_context`、`send_message`、`compress_context`
- WebSocket 实时消息监听 + 自动重连
- 消息按自然语义分段发送（句号/逗号/破折号等），模拟真人打字节奏
- 支持 AI 自主控制消息拆分段数（`num_chunks` 参数，按标点拆分后合并为指定段数）
- 群/好友白名单控制
- 发送速率限制（3s/目标）

## 前置条件

- 已安装并运行的原生 NapCatQQ（OneBot v11）
- Python 3.11+
- [uv](https://github.com/astral-sh/uv)

PetGPT 的内置 QQ Connector 会按需管理 uv、Python 和本项目，用户不需要手动安装
Python，也不使用 Docker。独立运行本项目时，可以使用 NapCat 官方提供的 Windows
OneKey/Shell、Linux AppImage 或 macOS Installer。

## 原生 NapCat 快速接入（推荐）

1. 启动原生 NapCat，并在 WebUI 中完成 QQ 扫码登录。
2. 创建仅监听 `127.0.0.1` 的 OneBot HTTP Server（默认端口 3000）。
3. 创建仅监听 `127.0.0.1` 的 OneBot WebSocket Server（默认端口 3001）。
4. 两个 Server 使用同一个随机 token。
5. 启动 MCP：

```bash
uv run qq-agent-mcp --qq 你的QQ号 --access-token 你的OneBot访问令牌
```

PetGPT 会自动完成第 2～5 步，并在注册前验证 Social Agent 依赖的工具契约。

---

## 旧版 Docker 快速开始（Windows，可选）

### 1. 配置 NapCat

```powershell
.\scripts\setup-windows.ps1
```

交互式引导你完成：
- 检查并自动启动 Docker Desktop
- 拉取 NapCat Docker 镜像
- 输入 QQ 号、设备名称
- 生成 `docker-compose.yml`
- 生成 OneBot11 接口配置（HTTP API 端口 3000 + WebSocket 端口 3001）
- 生成 `mcp.json`

### 2. 启动 NapCat

```powershell
.\scripts\start-docker-windows.ps1
```

首次启动需扫码登录，查看二维码：

```powershell
docker compose logs -f napcat
```

或访问 WebUI：http://localhost:6099

### 3. 启动 MCP Server

```powershell
uv run qq-agent-mcp --qq 你的QQ号
```

### 4. 配置 MCP 客户端

`setup-windows.ps1` 已自动生成 `mcp.json`：

```json
{
  "mcpServers": {
    "qq-agent": {
      "command": "C:/Users/你的用户名/.local/bin/uv.exe",
      "args": "run --directory C:/path/to/Amadeus-QQ-MCP qq-agent-mcp --qq 你的QQ号"
    }
  }
}
```

将 `mcp.json` 的内容复制到你的 AI 客户端的 MCP 配置中即可。

---

## 旧版 Docker 快速开始（Linux，可选）

### 1. 安装依赖

```bash
scripts/install-linux.sh
```

自动安装 Docker、uv，初始化项目配置并安装 Python 依赖。安装完成后需要 `source ~/.bashrc` 或打开新终端让 `uv` 命令生效。

### 2. 配置 NapCat

```bash
scripts/setup-linux.sh
```

交互式引导你完成：
- 拉取 NapCat Docker 镜像
- 输入 QQ 号、设备名称、UID/GID
- 生成 `docker-compose.yml`
- 生成 OneBot11 接口配置（HTTP API 端口 3000 + WebSocket 端口 3001）

### 3. 启动 NapCat

```bash
scripts/start-docker-linux.sh
```

首次启动需扫码登录，查看二维码：

```bash
sudo docker compose logs -f napcat
```

> 设置 `ACCOUNT` 后，配合 `restart: always` 和登录态持久化（`./napcat/qq-data`），可实现掉线后自动重连。仅当登录 token 过期时才需重新扫码。

### 4. 测试连接

```bash
uv run python scripts/test-mcp-linux.py
```

自动从 `docker-compose.yml` 读取 QQ 号，依次测试：MCP 握手 → 工具列表 → check_status。也可手动指定：

```bash
uv run python scripts/test-mcp-linux.py --qq 你的QQ号
```

### 5. 启动 MCP Server

```bash
# 最小参数
uv run qq-agent-mcp --qq 你的QQ号

# 指定监听群和好友
uv run qq-agent-mcp --qq 你的QQ号 --groups 群号1,群号2 --friends 好友QQ1,好友QQ2

# 全部参数
uv run qq-agent-mcp --qq 你的QQ号 \
  --napcat-host 127.0.0.1 \
  --napcat-port 3000 \
  --ws-port 3001 \
  --access-token 你的OneBot访问令牌 \
  --groups 群号1,群号2 \
  --friends 好友QQ1 \
  --buffer-size 100 \
  --log-level info
```

`--access-token` 也可以通过 `NAPCAT_ACCESS_TOKEN` 环境变量提供。建议为
NapCat 的 OneBot HTTP 和 WebSocket 服务配置同一个随机访问令牌，并只监听
本机地址。

### 6. 配置 MCP 客户端

`scripts/setup-linux.sh` 已自动在项目根目录生成 `mcp.json`，默认监听所有群：

```json
{
  "mcpServers": {
    "qq-agent": {
      "command": "/home/你的用户名/.local/bin/uv",
      "args": "run --directory /path/to/Amadeus-QQ-MCP qq-agent-mcp --qq 你的QQ号"
    }
  }
}
```

将 `mcp.json` 的内容复制到你的 AI 客户端的 MCP 配置中即可。

如需指定监听的群，在 `args` 中添加 `"--groups"`, `"群号1,群号2"`。

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
| `batch_get_recent_context(targets, limit?)` | 批量查询多个群/好友的消息上下文（最多 2 次 API 调用） |
| `send_message(target, content, target_type?, reply_to?, split_content?, num_chunks?)` | 发消息，自动分段+打字延迟。`num_chunks` 可指定恰好拆为几段（先按标点拆再合并） |
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
Amadeus-QQ-MCP/
├── src/qq_agent_mcp/           # MCP Server 源码
├── scripts/                    # 辅助脚本
│   ├── install-linux.sh        # 一键安装 (Linux)
│   ├── setup-linux.sh          # NapCat 配置 (Linux)
│   ├── start-docker-linux.sh   # 启动 Docker (Linux)
│   ├── test-mcp-linux.py       # MCP 连接测试 (Linux)
│   ├── setup-windows.ps1       # NapCat 配置 (Windows)
│   └── start-docker-windows.ps1 # 启动 Docker (Windows)
├── napcat/                     # NapCat Docker 挂载目录
│   ├── config/                 # NapCat + OneBot 配置
│   └── qq-data/                # QQ 登录态持久化
├── tests/                      # 集成测试
├── docker-compose.sample.yml   # Docker Compose 模板
├── mcp.json                    # MCP 客户端配置（setup 自动生成）
├── pyproject.toml              # Python 项目配置
└── README.md
```

## License

MIT
