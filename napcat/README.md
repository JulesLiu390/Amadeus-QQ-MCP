# napcat/

NapCat Docker 容器的 volume 挂载目录。此文件夹下的内容由容器运行时生成，已被 `.gitignore` 排除。

## 目录结构

```
napcat/
├── config/          # NapCat 配置文件（由容器自动生成，也可手动编辑）
│   ├── napcat.json                      # 全局配置（日志级别、packet 后端等）
│   ├── napcat_<QQ号>.json               # 账号级全局配置（同上，按账号覆盖）
│   ├── napcat_protocol_<QQ号>.json      # 协议配置
│   ├── onebot11.json                    # OneBot v11 网络配置模板
│   ├── onebot11_<QQ号>.json             # OneBot v11 网络配置（HTTP/WS 端口、token 等）
│   └── webui.json                       # NapCat WebUI 配置（端口、token、自动登录账号）
├── qq-data/         # QQ 客户端运行时数据（登录态、缓存、崩溃日志）
└── README.md        # 本文件
```

## 首次部署

1. 启动容器：`docker compose up -d`
2. 容器会自动在 `config/` 下生成默认配置文件
3. 访问 WebUI (`http://localhost:6099`) 扫码登录
4. 登录后 `qq-data/` 会保存登录态

## 关键配置项

| 文件 | 字段 | 说明 |
|------|------|------|
| `webui.json` | `autoLoginAccount` | 设为 QQ 号可自动登录，免重启扫码 |
| `onebot11_<QQ号>.json` | `network.httpServers[0].port` | OneBot HTTP API 端口（默认 3000） |
| `onebot11_<QQ号>.json` | `network.websocketServers[0].port` | OneBot WebSocket 端口（默认 3001） |
| `webui.json` | `token` | WebUI 登录密码 |

## 注意

- `qq-data/` 包含 QQ 登录凭据，**不要提交到 Git**
- `config/` 包含 token 等敏感信息，同样不应提交
- 两个目录下的 `.gitkeep` 仅用于保留文件夹结构
