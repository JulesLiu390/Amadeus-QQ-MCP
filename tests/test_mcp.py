"""Quick test: start the MCP server and call check_status via stdio."""

import asyncio
import json
import sys


async def main():
    proc = await asyncio.create_subprocess_exec(
        "uv", "run", "qq-agent-mcp", "--qq", "3825478002",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=10 * 1024 * 1024,  # 10 MB buffer for base64 image responses
    )

    # Give the server a moment to start
    await asyncio.sleep(1)

    # Check if process died
    if proc.returncode is not None:
        stderr = await proc.stderr.read()
        print(f"Server exited with code {proc.returncode}")
        print(f"stderr: {stderr.decode()}")
        return

    # MCP initialize
    init_msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0.1.0"},
        },
    }
    await send(proc, init_msg)
    resp = await recv_skip_notifications(proc, timeout=5)
    print("Init response:", json.dumps(resp, indent=2, ensure_ascii=False))

    # Send initialized notification
    await send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    await asyncio.sleep(0.3)

    # List tools
    await send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    resp = await recv_skip_notifications(proc, timeout=5)
    tools = [t["name"] for t in resp.get("result", {}).get("tools", [])]
    print(f"\nTools: {tools}")

    # Call check_status
    await send(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "check_status", "arguments": {}},
        },
    )
    resp = await recv_skip_notifications(proc, timeout=10)
    print("\ncheck_status result:")
    for c in resp.get("result", {}).get("content", []):
        if c["type"] == "text":
            data = json.loads(c["text"])
            print(json.dumps(data, indent=2, ensure_ascii=False))
    # Wait a few seconds for WS listener to collect some messages
    print("\nWaiting 5s for WebSocket messages to arrive...")
    await asyncio.sleep(15)

    # Call get_recent_context on a test group
    await send(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "get_recent_context",
                "arguments": {"target": "1059558644", "target_type": "group", "limit": 5},
            },
        },
    )
    resp = await recv_skip_notifications(proc, timeout=30)
    print("\nget_recent_context result:")
    for c in resp.get("result", {}).get("content", []):
        if c["type"] == "text":
            text = c["text"]
            try:
                data = json.loads(text)
                # Truncate base64 image data for readability
                for msg in data.get("messages", []):
                    for img in msg.get("images", []):
                        if "data" in img and len(img["data"]) > 40:
                            img["data"] = img["data"][:40] + f"... ({len(img['data'])} chars)"
                print(json.dumps(data, indent=2, ensure_ascii=False))
            except json.JSONDecodeError:
                # Image label text (not JSON)
                print(f"  {text}")
        elif c["type"] == "image":
            data_str = c.get("data", "")
            mime = c.get("mimeType", "?")
            if data_str.startswith("http"):
                print(f"  [ImageContent: {mime}, url={data_str}]")
            else:
                print(f"  [ImageContent: {mime}, {len(data_str)} chars base64]")

    # Test send_message with long content (chunking test)
    long_content = (
        "这是一条测试消息，用来验证消息自动拆分功能。\n\n"
        "第一段：OpenClaw 的消息拆分机制非常优雅。它通过 EmbeddedBlockChunker 将长文本按段落、换行、句号等自然边界拆分成多条消息，"
        "每条之间加入 800-2500ms 的随机延迟，模拟真人打字节奏。这种方式让 AI 的回复看起来更加自然，而不是一次性发出一大坨文字。\n\n"
        "第二段：我们的 QQ MCP Server 也实现了类似的机制。当 AI 回复的内容超过 500 字符时，"
        "会自动按照段落边界 > 换行符 > 句号 > 空格的优先级进行拆分。每条消息之间会有随机延迟，让对话更像真人。\n\n"
        "第三段：这条消息本身就是一个测试用例。如果你看到这条消息被拆成了多条，说明拆分功能工作正常！✅"
    )
    print("\n--- Testing send_message (chunking) ---")
    print(f"Content length: {len(long_content)} chars")
    await send(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "send_message",
                "arguments": {"target": "1059558644", "target_type": "group", "content": long_content},
            },
        },
    )
    resp = await recv_skip_notifications(proc, timeout=120)
    print("send_message result:")
    for c in resp.get("result", {}).get("content", []):
        if c["type"] == "text":
            try:
                print(json.dumps(json.loads(c["text"]), indent=2, ensure_ascii=False))
            except json.JSONDecodeError:
                print(c["text"])

    proc.terminate()
    print("\nDone!")


async def send(proc, msg):
    """Send a JSON-RPC message as a single line (NDJSON)."""
    line = json.dumps(msg) + "\n"
    proc.stdin.write(line.encode())
    await proc.stdin.drain()


async def recv(proc, timeout=5):
    """Read one line of NDJSON from stdout."""
    line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
    if not line:
        raise RuntimeError("Server closed stdout")
    return json.loads(line)


async def recv_skip_notifications(proc, timeout=5):
    """Read lines, skipping server notifications, until we get a response (has 'id')."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError("No response received")
        msg = await recv(proc, timeout=remaining)
        if "id" in msg:
            return msg
        # It's a notification, skip it
        print(f"  [notification] {msg.get('method', '?')}: {json.dumps(msg.get('params', {}), ensure_ascii=False)}")


if __name__ == "__main__":
    asyncio.run(main())
