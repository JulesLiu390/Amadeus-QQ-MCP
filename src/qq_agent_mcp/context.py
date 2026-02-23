"""Message buffer & WebSocket listener for QQ message context."""

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

import aiohttp

from .config import Config

logger = logging.getLogger(__name__)

# China Standard Time offset
CST = timezone(timedelta(hours=8))


@dataclass
class Message:
    """Standardized message format."""

    sender_id: str
    sender_name: str
    content: str
    timestamp: str  # ISO 8601
    message_id: str
    is_at_me: bool = False
    is_self: bool = False
    image_urls: list[str] = field(default_factory=list)
    received_at: float = field(default_factory=time.time)  # local monotonic clock

    def to_dict(self) -> dict:
        d = {
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "content": self.content,
            "timestamp": self.timestamp,
            "message_id": str(self.message_id),
            "is_at_me": self.is_at_me,
            "is_self": self.is_self,
        }
        if self.image_urls:
            d["image_urls"] = self.image_urls
        return d


class MessageBuffer:
    """Per-target sliding window message buffer with compression."""

    def __init__(self, maxlen: int = 100, compress_every: int = 30):
        self.messages: deque[Message] = deque(maxlen=maxlen)
        self._seen_ids: set[str] = set()  # for dedup by message_id
        self.compressed_summary: str | None = None
        self._msg_since_compress: int = 0
        self._compress_every = compress_every
        self._compress_pending = False
        self._compress_all_pending = False

    def add(self, msg: Message) -> None:
        """Add a message with dedup by message_id.

        Marks compression as pending when threshold is reached.
        """
        if msg.message_id and msg.message_id in self._seen_ids:
            return  # duplicate (e.g. direct write + WebSocket echo)
        if msg.message_id:
            self._seen_ids.add(msg.message_id)
            # Prevent unbounded growth — trim oldest IDs when set is large
            max_ids = (self.messages.maxlen or 100) * 2
            if len(self._seen_ids) > max_ids:
                self._seen_ids = {m.message_id for m in self.messages if m.message_id}
        self.messages.append(msg)
        self._msg_since_compress += 1

        if self._msg_since_compress >= self._compress_every:
            self._compress_pending = True

    def mark_all_for_compress(self) -> None:
        """Mark all current messages for compression (used after backfill)."""
        if self.messages:
            self._compress_all_pending = True

    def extract_oldest_for_compress(self) -> list[Message] | None:
        """Extract the oldest batch of messages for compression. Returns None if not needed."""
        # Backfill case: compress ALL messages
        if self._compress_all_pending:
            if not self.messages:
                self._compress_all_pending = False
                return None
            old_msgs = list(self.messages)
            self.messages.clear()
            self._compress_all_pending = False
            self._compress_pending = False
            self._msg_since_compress = 0
            return old_msgs

        if not self._compress_pending:
            return None
        if len(self.messages) < self._compress_every:
            self._compress_pending = False
            self._msg_since_compress = 0
            return None

        n_to_compress = min(self._compress_every, len(self.messages) // 2)
        if n_to_compress == 0:
            self._compress_pending = False
            self._msg_since_compress = 0
            return None

        old_msgs = []
        for _ in range(n_to_compress):
            old_msgs.append(self.messages.popleft())

        self._compress_pending = False
        self._msg_since_compress = 0
        return old_msgs

    def apply_summary(self, new_summary: str) -> None:
        """Append a compressed summary block."""
        if self.compressed_summary:
            self.compressed_summary = self.compressed_summary + "\n" + new_summary
        else:
            self.compressed_summary = new_summary

        logger.debug("Summary updated. Length: %d", len(self.compressed_summary))

    def get_recent(self, limit: int = 20) -> list[dict]:
        """Return the most recent `limit` messages as dicts."""
        msgs = list(self.messages)
        return [m.to_dict() for m in msgs[-limit:]]

    def get_since(self, since: float) -> list[Message]:
        """Return messages with received_at >= since."""
        return [m for m in self.messages if m.received_at >= since]

    @property
    def count(self) -> int:
        return len(self.messages)


# Forward message expansion limits
_FORWARD_MAX_DEPTH = 2
_FORWARD_MAX_MESSAGES = 20


class ContextManager:
    """Manages message buffers and the WebSocket event listener."""

    def __init__(self, config: Config, bot=None):
        self.config = config
        self.bot = bot  # OneBotClient, set before start()
        self._buffers: dict[str, MessageBuffer] = {}
        self._ws_task: asyncio.Task | None = None
        self._running = False

    def _buffer_key(self, target_type: str, target_id: str) -> str:
        return f"{target_type}:{target_id}"

    def _get_or_create_buffer(self, key: str) -> MessageBuffer:
        if key not in self._buffers:
            self._buffers[key] = MessageBuffer(
                maxlen=self.config.buffer_size,
                compress_every=self.config.compress_every,
            )
        return self._buffers[key]

    # ── Public API ──────────────────────────────────────────

    def start(self) -> None:
        """Start the background WebSocket listener task."""
        if self._ws_task is not None:
            return
        self._running = True
        self._ws_task = asyncio.get_event_loop().create_task(self._ws_loop())
        logger.info("WebSocket listener started (target: %s)", self.config.ws_url)

    async def backfill_history(self, bot) -> None:
        """Pull recent history for all monitored groups via HTTP API."""
        try:
            groups = await bot.get_group_list()
        except Exception as e:
            logger.warning("Failed to get group list for backfill: %s", e)
            return

        count = 0
        for g in groups:
            gid = str(g.get("group_id", ""))
            if not self.config.is_group_monitored(gid):
                continue
            try:
                messages = await bot.get_group_msg_history(gid, count=self.config.buffer_size)
                key = self._buffer_key("group", gid)
                buf = self._get_or_create_buffer(key)
                for event in messages:
                    sender_id = str(event.get("user_id", event.get("sender", {}).get("user_id", "")))
                    is_self = sender_id == self.config.qq
                    content, is_at_me, image_urls = await self._parse_message_segments(event.get("message", []))
                    if not content.strip():
                        continue
                    sender_name = (
                        event.get("sender", {}).get("card")
                        or event.get("sender", {}).get("nickname")
                        or sender_id
                    )
                    msg = Message(
                        sender_id=sender_id,
                        sender_name=sender_name,
                        content=content,
                        timestamp=self._format_timestamp(event.get("time", 0)),
                        message_id=str(event.get("message_id", "")),
                        is_at_me=is_at_me,
                        is_self=is_self,
                        image_urls=image_urls,
                    )
                    buf.messages.append(msg)
                    count += 1
                logger.info("Backfilled %d messages for group %s", len(buf.messages), gid)
            except Exception as e:
                logger.warning("Failed to backfill group %s: %s", gid, e)

        logger.info("History backfill complete: %d messages across %d groups", count, len(self._buffers))

    async def stop(self) -> None:
        """Stop the WebSocket listener."""
        self._running = False
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None
        logger.info("WebSocket listener stopped")

    def get_context(
        self,
        target: str,
        target_type: str = "group",
        limit: int = 20,
    ) -> dict:
        """Get message context for a target. Returns a dict ready for MCP tool response."""
        key = self._buffer_key(target_type, target)
        buf = self._buffers.get(key)

        if buf is None:
            return {
                "target": target,
                "target_type": target_type,
                "compressed_summary": None,
                "message_count": 0,
                "messages": [],
            }

        return {
            "target": target,
            "target_type": target_type,
            "compressed_summary": buf.compressed_summary,
            "message_count": buf.count,
            "messages": buf.get_recent(limit),
        }

    def add_message(
        self, target: str, target_type: str, msg: Message,
    ) -> None:
        """Directly add a message to the buffer for a target."""
        key = self._buffer_key(target_type, target)
        buf = self._get_or_create_buffer(key)
        buf.add(msg)

    def get_messages_since(
        self, target: str, target_type: str, since: float,
    ) -> list[Message]:
        """Return messages received after `since` for a target."""
        key = self._buffer_key(target_type, target)
        buf = self._buffers.get(key)
        if buf is None:
            return []
        return buf.get_since(since)

    @property
    def buffer_stats(self) -> dict:
        """Summary stats for check_status."""
        total = sum(b.count for b in self._buffers.values())
        groups = sum(1 for k in self._buffers if k.startswith("group:"))
        friends = sum(1 for k in self._buffers if k.startswith("private:"))
        return {
            "total_messages_buffered": total,
            "groups_tracked": groups,
            "friends_tracked": friends,
        }

    # ── WebSocket Loop ──────────────────────────────────────

    async def _ws_loop(self) -> None:
        """Reconnecting WebSocket listener loop."""
        retry_delay = 1.0  # seconds, grows on failure
        max_retry = 30.0

        while self._running:
            try:
                async with aiohttp.ClientSession() as session:
                    logger.info("Connecting to WebSocket: %s", self.config.ws_url)
                    async with session.ws_connect(self.config.ws_url) as ws:
                        logger.info("WebSocket connected")
                        retry_delay = 1.0  # reset on success
                        async for raw_msg in ws:
                            if raw_msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    event = json.loads(raw_msg.data)
                                    await self._handle_event(event)
                                except json.JSONDecodeError:
                                    logger.warning("Invalid JSON from WS: %s", raw_msg.data[:200])
                            elif raw_msg.type == aiohttp.WSMsgType.ERROR:
                                logger.error("WS error: %s", ws.exception())
                                break
                            elif raw_msg.type in (
                                aiohttp.WSMsgType.CLOSE,
                                aiohttp.WSMsgType.CLOSING,
                                aiohttp.WSMsgType.CLOSED,
                            ):
                                logger.warning("WS connection closed")
                                break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("WebSocket connection error: %s", e)

            if self._running:
                logger.info("Reconnecting in %.1fs...", retry_delay)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry)

    # ── Event Handling ──────────────────────────────────────

    async def _handle_event(self, event: dict) -> None:
        """Route an OneBot v11 event to the appropriate handler."""
        post_type = event.get("post_type")
        if post_type != "message":
            return  # Only handle message events

        msg_type = event.get("message_type")
        if msg_type == "group":
            await self._handle_group_message(event)
        elif msg_type == "private":
            await self._handle_private_message(event)

    async def _handle_group_message(self, event: dict) -> None:
        """Process a group message event."""
        group_id = str(event.get("group_id", ""))
        sender_id = str(event.get("user_id", event.get("sender", {}).get("user_id", "")))

        # Whitelist check
        if not self.config.is_group_monitored(group_id):
            return

        is_self = sender_id == self.config.qq

        # Parse message content and @detection
        content, is_at_me, image_urls = await self._parse_message_segments(event.get("message", []))
        if not content.strip():
            return  # Skip empty messages

        sender_name = (
            event.get("sender", {}).get("card")
            or event.get("sender", {}).get("nickname")
            or sender_id
        )

        timestamp = self._format_timestamp(event.get("time", 0))
        message_id = str(event.get("message_id", ""))

        msg = Message(
            sender_id=sender_id,
            sender_name=sender_name,
            content=content,
            timestamp=timestamp,
            message_id=message_id,
            is_at_me=is_at_me,
            is_self=is_self,
            image_urls=image_urls,
        )

        key = self._buffer_key("group", group_id)
        buf = self._get_or_create_buffer(key)
        buf.add(msg)

        logger.debug(
            "Group %s | %s: %s%s",
            group_id,
            sender_name,
            content[:50],
            " [@me]" if is_at_me else "",
        )

    async def _handle_private_message(self, event: dict) -> None:
        """Process a private message event."""
        sender_id = str(event.get("user_id", event.get("sender", {}).get("user_id", "")))

        # Whitelist check — friends list must be explicitly set
        if not self.config.is_friend_monitored(sender_id):
            return

        is_self = sender_id == self.config.qq

        content, _, image_urls = await self._parse_message_segments(event.get("message", []))
        if not content.strip():
            return

        sender_name = event.get("sender", {}).get("nickname", sender_id)
        timestamp = self._format_timestamp(event.get("time", 0))
        message_id = str(event.get("message_id", ""))

        msg = Message(
            sender_id=sender_id,
            sender_name=sender_name,
            content=content,
            timestamp=timestamp,
            message_id=message_id,
            is_self=is_self,
            image_urls=image_urls,
        )

        key = self._buffer_key("private", sender_id)
        buf = self._get_or_create_buffer(key)
        buf.add(msg)

        logger.debug("Private %s | %s: %s", sender_id, sender_name, content[:50])

    # ── Helpers ───────────────────────────────────────────

    def _find_message_in_buffers(self, message_id: str) -> Message | None:
        """Search all buffers for a message by its message_id (O(n) scan, zero I/O)."""
        for buf in self._buffers.values():
            for msg in reversed(buf.messages):  # recent first
                if msg.message_id == message_id:
                    return msg
        return None

    async def _resolve_reply(
        self, reply_id: str, depth: int,
    ) -> str:
        """Resolve a reply segment into human-readable text.

        Scheme C: buffer lookup first, then API fallback.
        Format E: [回复了 名字(QQ号) 的「内容前50字」]
        """
        MAX_QUOTE_LEN = 50

        # --- fast path: buffer lookup ---
        cached = self._find_message_in_buffers(reply_id)
        if cached:
            quote = cached.content[:MAX_QUOTE_LEN]
            if len(cached.content) > MAX_QUOTE_LEN:
                quote += "…"
            return f"[回复了 {cached.sender_name}({cached.sender_id}) 的「{quote}」] "

        # --- slow path: API fallback ---
        if not self.bot:
            return "[回复了 未知消息] "

        try:
            event = await self.bot.get_msg(reply_id)
        except Exception as e:
            logger.warning("Failed to get_msg %s for reply expansion: %s", reply_id, e)
            return "[回复了 未知消息] "

        if not event:
            return "[回复了 未知消息] "

        # Parse sender
        sender = event.get("sender", {})
        sender_name = sender.get("card") or sender.get("nickname") or str(sender.get("user_id", "?"))
        sender_id = str(event.get("user_id", sender.get("user_id", "?")))

        # Parse content (non-recursive for reply — depth+1 to avoid infinite loops)
        raw_msg = event.get("message", [])
        if isinstance(raw_msg, str):
            content_text = raw_msg
        else:
            # Strip reply segments from the referenced message to avoid nested reply expansion
            filtered = [s for s in raw_msg if s.get("type") != "reply"]
            content_text, _, _ = await self._parse_message_segments(filtered, _depth=depth + 1)

        quote = content_text[:MAX_QUOTE_LEN]
        if len(content_text) > MAX_QUOTE_LEN:
            quote += "…"

        return f"[回复了 {sender_name}({sender_id}) 的「{quote}」] "

    # ── Message Parsing ─────────────────────────────────────

    async def _parse_message_segments(
        self, segments: list, _depth: int = 0,
    ) -> tuple[str, bool, list[str]]:
        """Parse OneBot v11 message segments into text content.

        Returns (content_string, is_at_me, image_urls).
        Handles both array format and plain string format.
        Expands forward messages up to _FORWARD_MAX_DEPTH layers.
        """
        if isinstance(segments, str):
            return segments, False, []

        parts: list[str] = []
        is_at_me = False
        image_urls: list[str] = []

        for seg in segments:
            seg_type = seg.get("type", "")
            data = seg.get("data", {})

            if seg_type == "text":
                parts.append(data.get("text", ""))
            elif seg_type == "at":
                qq = str(data.get("qq", ""))
                if qq == self.config.qq or qq == "all":
                    is_at_me = True
                    parts.append("@me")
                else:
                    name = data.get("name", qq)
                    parts.append(f"@{name}")
            elif seg_type == "image":
                url = data.get("url", "")
                if url:
                    image_urls.append(url)
                parts.append("[图片]")
            elif seg_type == "face":
                face_id = data.get("id", "?")
                parts.append(f"[表情{face_id}]")
            elif seg_type == "reply":
                reply_id = data.get("id", "")
                if reply_id:
                    reply_text = await self._resolve_reply(reply_id, _depth)
                    parts.append(reply_text)
                else:
                    parts.append("[回复了 未知消息] ")
            elif seg_type == "record":
                parts.append("[语音]")
            elif seg_type == "video":
                parts.append("[视频]")
            elif seg_type == "forward":
                forward_text = await self._expand_forward(data, _depth)
                parts.append(forward_text)
            elif seg_type == "json":
                raw = data.get("data", "")
                try:
                    card = json.loads(raw) if isinstance(raw, str) else raw
                    prompt = (card.get("prompt") or "").strip()
                    desc = (card.get("desc") or "").strip()
                    if prompt and desc:
                        label = f"{desc} - {prompt}"
                    elif prompt:
                        label = prompt
                    else:
                        label = None
                    if label:
                        if len(label) > 80:
                            label = label[:80] + "…"
                        parts.append(f"[卡片: {label}]")
                    else:
                        parts.append("[卡片消息]")
                except (json.JSONDecodeError, TypeError, AttributeError):
                    parts.append("[卡片消息]")
            elif seg_type == "file":
                parts.append(f"[文件: {data.get('name', '?')}]")
            # Other types are silently dropped

        content = "".join(parts).strip()
        return content, is_at_me, image_urls

    async def _expand_forward(self, data: dict, depth: int) -> str:
        """Expand a forward message into readable text.

        Args:
            data: The forward segment's data dict (contains 'id').
            depth: Current recursion depth (0 = top level).

        Returns:
            Formatted string like:
              (转发消息 共N条):
                [02-19 14:30] 小明(123456): 你好
                [02-19 14:31] 小红(789012): 嗯嗯
                ...省略 M 条
        """
        if depth >= _FORWARD_MAX_DEPTH:
            return "[嵌套转发消息]"

        forward_id = data.get("id", "")
        if not forward_id or not self.bot:
            return "[转发消息]"

        try:
            nodes = await self.bot.get_forward_msg(forward_id)
        except Exception as e:
            logger.warning("Failed to fetch forward msg %s: %s", forward_id, e)
            return "[转发消息]"

        if not nodes:
            return "[转发消息(空)]"

        total = len(nodes)
        indent = "  " * (depth + 1)
        lines: list[str] = []
        count = 0

        for node in nodes:
            if count >= _FORWARD_MAX_MESSAGES:
                break

            # Extract sender info
            sender = node.get("sender", {})
            sender_name = sender.get("nickname", sender.get("card", "?"))
            sender_id = str(sender.get("user_id", "?"))
            ts = self._format_short_timestamp(node.get("time", 0))

            # Parse nested content (may contain further forwards)
            node_content = node.get("content", node.get("message", []))
            if isinstance(node_content, list):
                text, _, _ = await self._parse_message_segments(node_content, _depth=depth + 1)
            elif isinstance(node_content, str):
                text = node_content
            else:
                text = str(node_content)

            # Truncate long content (single-line: 50 chars, with nested forward: 500 chars)
            max_len = 500 if "\n" in text else 50
            if len(text) > max_len:
                text = text[:max_len] + "..."

            lines.append(f"{indent}[{ts}] {sender_name}({sender_id}): {text}")
            count += 1

        header = f"(转发消息 共{total}条):"
        result = header + "\n" + "\n".join(lines)
        if total > _FORWARD_MAX_MESSAGES:
            result += f"\n{indent}...省略{total - _FORWARD_MAX_MESSAGES}条"
        return result

    @staticmethod
    def _format_short_timestamp(unix_ts: int) -> str:
        """Convert Unix timestamp to short MM-DD HH:MM format in CST."""
        if unix_ts <= 0:
            return "??-?? ??:??"
        dt = datetime.fromtimestamp(unix_ts, tz=CST)
        return dt.strftime("%m-%d %H:%M")

    @staticmethod
    def _format_timestamp(unix_ts: int) -> str:
        """Convert Unix timestamp to ISO 8601 string in CST."""
        if unix_ts <= 0:
            return datetime.now(CST).isoformat()
        return datetime.fromtimestamp(unix_ts, tz=CST).isoformat()
