"""Quick test: send a message via MCP send_message tool."""

import asyncio
import json


async def send(proc, msg):
    line = json.dumps(msg) + "\n"
    proc.stdin.write(line.encode())
    await proc.stdin.drain()


async def recv(proc, timeout=5):
    line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
    if not line:
        raise RuntimeError("Server closed stdout")
    return json.loads(line)


async def recv_skip_notifications(proc, timeout=10):
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError("No response received")
        msg = await recv(proc, timeout=remaining)
        if "id" in msg:
            return msg
        print(f"  [notification] {msg.get('method', '?')}")


async def main():
    proc = await asyncio.create_subprocess_exec(
        "uv", "run", "qq-agent-mcp", "--qq", "3825478002",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=10 * 1024 * 1024,
    )
    await asyncio.sleep(1)
    if proc.returncode is not None:
        stderr = await proc.stderr.read()
        print(f"Server exited: {proc.returncode}, stderr: {stderr.decode()[:500]}")
        return

    # Initialize
    await send(proc, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0.1"},
        },
    })
    resp = await recv_skip_notifications(proc, timeout=15)
    print("Init OK")

    await send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    await asyncio.sleep(0.5)

    # Check status
    await send(proc, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "check_status", "arguments": {}},
    })
    resp = await recv_skip_notifications(proc, timeout=15)
    for c in resp.get("result", {}).get("content", []):
        if c["type"] == "text":
            data = json.loads(c["text"])
            print(f"Status: online={data.get('online_status')}, qq={data.get('qq_account')}, nickname={data.get('qq_nickname')}")

    await asyncio.sleep(1)

    # Send test message
    print("\nSending test message to group 1059558644...")
    await send(proc, {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {
            "name": "send_message",
            "arguments": {
                "target": "1059558644",
                "target_type": "group",
                "content": "MCP send test 🧪",
            },
        },
    })
    resp = await recv_skip_notifications(proc, timeout=30)
    print("send_message result:")
    for c in resp.get("result", {}).get("content", []):
        if c["type"] == "text":
            print(json.dumps(json.loads(c["text"]), indent=2, ensure_ascii=False))

    proc.terminate()
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
