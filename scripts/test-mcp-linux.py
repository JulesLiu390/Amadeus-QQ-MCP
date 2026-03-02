#!/usr/bin/env python3
"""
MCP 连接测试脚本 (Linux)

启动 MCP Server 子进程，通过 stdio JSON-RPC 协议依次执行：
  1. initialize 握手
  2. tools/list  列出所有工具
  3. check_status 检查 QQ 登录状态

用法:
  python3 scripts/test-mcp-linux.py              # 自动从 docker-compose.yml 读取 QQ 号
  python3 scripts/test-mcp-linux.py --qq <QQ号>   # 手动指定

无需 uv，纯 Python 标准库即可运行。
"""

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import os

# 颜色输出
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"


def ok(msg: str) -> None:
    print(f"  {GREEN}✅ {msg}{RESET}")


def fail(msg: str) -> None:
    print(f"  {RED}❌ {msg}{RESET}")


def info(msg: str) -> None:
    print(f"  {CYAN}ℹ️  {msg}{RESET}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}⚠️  {msg}{RESET}")


async def send(proc, msg: dict) -> None:
    """发送一条 JSON-RPC 消息（NDJSON 格式）。"""
    line = json.dumps(msg) + "\n"
    proc.stdin.write(line.encode())
    await proc.stdin.drain()


async def recv(proc, timeout: float = 5.0) -> dict:
    """读取一行 JSON-RPC 响应。"""
    line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
    if not line:
        raise RuntimeError("Server 关闭了 stdout")
    return json.loads(line)


async def recv_response(proc, timeout: float = 10.0) -> dict:
    """读取响应，跳过服务端通知（没有 id 的消息）。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError("等待响应超时")
        msg = await recv(proc, timeout=remaining)
        if "id" in msg:
            return msg


async def main(qq: str) -> int:
    """运行测试，返回 0 表示全部通过，1 表示有失败。"""
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(project_dir)

    print(f"\n{BOLD}=== MCP 连接测试 ==={RESET}\n")

    # ── 启动 MCP Server ────────────────────────────────────
    print(f"{BOLD}[1/4] 启动 MCP Server ...{RESET}")
    cmd = _find_run_cmd()
    if cmd is None:
        fail("找不到 uv 或 qq-agent-mcp，请先运行 scripts/install-linux.sh")
        return 1
    args_cmd = cmd + ["--qq", qq]
    info(f"启动命令: {' '.join(args_cmd)}")
    try:
        proc = await asyncio.create_subprocess_exec(
            *args_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        fail(f"命令不存在: {args_cmd[0]}")
        return 1

    # 等待启动
    await asyncio.sleep(1)

    if proc.returncode is not None:
        stderr = (await proc.stderr.read()).decode()
        fail(f"Server 启动失败 (exit code {proc.returncode})")
        print(f"    stderr: {stderr[:500]}")
        return 1

    ok("Server 进程已启动")
    passed = 0
    total = 3

    try:
        # ── 测试 1: MCP initialize 握手 ────────────────────
        print(f"\n{BOLD}[2/4] MCP 协议握手 (initialize) ...{RESET}")
        await send(proc, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-mcp", "version": "0.1.0"},
            },
        })
        resp = await recv_response(proc, timeout=15)

        if "result" in resp:
            server_info = resp["result"].get("serverInfo", {})
            protocol = resp["result"].get("protocolVersion", "?")
            ok(f"握手成功 — server: {server_info.get('name', '?')}, protocol: {protocol}")
            passed += 1
        else:
            error = resp.get("error", {})
            fail(f"握手失败: {error.get('message', resp)}")

        # 发送 initialized 通知
        await send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        await asyncio.sleep(0.3)

        # ── 测试 2: 列出 MCP 工具 ─────────────────────────
        print(f"\n{BOLD}[3/4] 获取工具列表 (tools/list) ...{RESET}")
        await send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        resp = await recv_response(proc, timeout=10)

        if "result" in resp:
            tools = resp["result"].get("tools", [])
            tool_names = [t["name"] for t in tools]
            ok(f"获取到 {len(tools)} 个工具: {', '.join(tool_names)}")
            passed += 1
        else:
            fail(f"获取工具列表失败: {resp.get('error', {}).get('message', resp)}")

        # ── 测试 3: 调用 check_status ─────────────────────
        print(f"\n{BOLD}[4/4] 调用 check_status ...{RESET}")
        await send(proc, {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "check_status", "arguments": {}},
        })
        resp = await recv_response(proc, timeout=15)

        if "result" in resp:
            contents = resp["result"].get("content", [])
            for c in contents:
                if c.get("type") == "text":
                    data = json.loads(c["text"])
                    napcat_ok = data.get("napcat_running", False)
                    qq_ok = data.get("qq_logged_in", False)
                    nickname = data.get("qq_nickname", "")
                    online = data.get("online_status", "unknown")
                    groups = data.get("monitored_groups", [])
                    friends = data.get("monitored_friends", [])

                    if napcat_ok and qq_ok:
                        ok(f"NapCat 运行中, QQ 已登录 — {nickname} ({data.get('qq_account', '')}), 状态: {online}")
                        ok(f"监控群: {len(groups)} 个, 监控好友: {len(friends)} 个")
                        passed += 1
                    elif napcat_ok:
                        warn(f"NapCat 运行中, 但 QQ 未登录")
                        info("请运行 'sudo docker compose logs -f napcat' 扫码登录")
                        passed += 1  # MCP 本身连接成功
                    else:
                        fail("NapCat 未运行")
                        info("请先运行 scripts/start-docker-linux.sh 启动 NapCat")
        else:
            fail(f"check_status 调用失败: {resp.get('error', {}).get('message', resp)}")

    except TimeoutError as e:
        fail(f"超时: {e}")
    except Exception as e:
        fail(f"异常: {e}")
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
        except asyncio.TimeoutError:
            proc.kill()

    # ── 汇总 ──────────────────────────────────────────────
    print(f"\n{BOLD}=== 测试结果: {passed}/{total} 通过 ==={RESET}")
    if passed == total:
        print(f"{GREEN}🎉 MCP Server 连接正常，一切就绪！{RESET}\n")
        return 0
    else:
        print(f"{YELLOW}部分测试未通过，请检查上方输出。{RESET}\n")
        return 1


def _find_run_cmd() -> list[str] | None:
    """查找可用的 MCP Server 启动命令。优先 uv，回退 python -m。"""
    if shutil.which("uv"):
        return ["uv", "run", "qq-agent-mcp"]
    # 回退: 检查当前 Python 能否 import
    try:
        subprocess.run(
            [sys.executable, "-c", "import qq_agent_mcp"],
            capture_output=True, check=True,
        )
        return [sys.executable, "-m", "qq_agent_mcp"]
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return None


def _read_qq_from_compose() -> str | None:
    """从 docker-compose.yml 中提取 ACCOUNT 值。"""
    compose_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "docker-compose.yml",
    )
    try:
        with open(compose_path) as f:
            for line in f:
                if "ACCOUNT=" in line:
                    val = line.split("ACCOUNT=", 1)[1].strip()
                    if val:
                        return val
    except FileNotFoundError:
        pass
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="测试 MCP Server 连接 (Linux)")
    parser.add_argument("--qq", default=None, help="QQ 号 (默认从 docker-compose.yml 读取)")
    args = parser.parse_args()

    qq = args.qq or _read_qq_from_compose()
    if not qq:
        fail("未指定 QQ 号。请使用 --qq 参数，或在 docker-compose.yml 中设置 ACCOUNT")
        sys.exit(1)

    sys.exit(asyncio.run(main(qq)))
