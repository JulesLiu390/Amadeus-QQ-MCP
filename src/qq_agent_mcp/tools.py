"""MCP Tools definitions."""

import asyncio
import hashlib
import logging
import random
import re
import time
import unicodedata
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Any

from mcp.server.fastmcp import Context
from mcp.types import SamplingMessage, TextContent

from .config import Config
from .context import ContextManager, Message
from .onebot import OneBotClient

logger = logging.getLogger(__name__)

# Rate limiter state: target -> last_send_timestamp
_last_send: dict[str, float] = {}
RATE_LIMIT_SECONDS = 0.0
CST = timezone(timedelta(hours=8))

# ── @QQ号 → real at segment ──────────────────────────────
_AT_RE = re.compile(r"(?<![a-zA-Z0-9.])@(\d{5,11})(?!\d)")
# ── CQ码表情 → real face segment ────────────────────────
_CQ_FACE_RE = re.compile(r"\[CQ:face,id=(\d+)\]")

# Combined pattern: match either @QQ号 or [CQ:face,id=N]
_SEGMENT_RE = re.compile(
    r"(?P<at>(?<![a-zA-Z0-9.])@(?P<qq>\d{5,11})(?!\d))"
    r"|(?P<face>\[CQ:face,id=(?P<face_id>\d+)\])"
)


def _text_to_segments(text: str) -> list[dict]:
    """Convert @QQ号 and [CQ:face,id=N] in text to OneBot segments."""
    segments: list[dict] = []
    last_end = 0
    for m in _SEGMENT_RE.finditer(text):
        if m.start() > last_end:
            segments.append({"type": "text", "data": {"text": text[last_end:m.start()]}})
        if m.group("at"):
            segments.append({"type": "at", "data": {"qq": m.group("qq")}})
        elif m.group("face"):
            segments.append({"type": "face", "data": {"id": m.group("face_id")}})
        last_end = m.end()
    if last_end < len(text):
        segments.append({"type": "text", "data": {"text": text[last_end:]}})
    return segments or [{"type": "text", "data": {"text": text}}]


# ── </分段> tag-based message splitting ────────────────
_SPLIT_TAG_RE = re.compile(r"</\s*分段\s*>")


def _split_by_tag(text: str) -> list[str] | None:
    """Split text on </分段> tags.

    Returns the list of non-empty, stripped segments if the tag is present;
    returns None if no tag was found (so callers can fall through to other
    splitting strategies).
    """
    if not text or not _SPLIT_TAG_RE.search(text):
        return None
    parts = _SPLIT_TAG_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


# ── Duplicate send detection ────────────────────────────
_DEDUP_WINDOW_SECONDS = 60.0  # 1 minute
# key = "target_type:target_id" -> deque of (content_hash, send_time)
_sent_history: dict[str, deque[tuple[str, float]]] = {}


def _normalize_content(text: str) -> str:
    """Normalize text for dedup comparison: strip, collapse whitespace, NFKC."""
    text = unicodedata.normalize("NFKC", text.strip())
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def _check_duplicate(target_key: str, content: str) -> str | None:
    """Check if content was sent to this target within the dedup window.

    Returns a warning message if duplicate, None otherwise.
    Automatically evicts expired entries.
    """
    now = time.time()
    h = hashlib.md5(_normalize_content(content).encode()).hexdigest()

    history = _sent_history.get(target_key)
    if history is None:
        history = deque(maxlen=50)
        _sent_history[target_key] = history

    # Evict expired entries
    while history and now - history[0][1] > _DEDUP_WINDOW_SECONDS:
        history.popleft()

    # Check for match
    for entry_hash, entry_time in history:
        if entry_hash == h:
            ago = int(now - entry_time)
            return (
                f"⚠️ 这条消息你在 {ago} 秒前已经发送过完全相同的内容，未重复发送。"
                f"如果确实需要重发，请稍作修改后重试。"
            )

    # Record this send
    history.append((h, now))
    return None

# Chunking config
CHUNK_MAX_CHARS = 30
# Delay: ms per character (scales with chunk length)
HUMAN_DELAY_MS_PER_CHAR = 80  # ~80ms per char ≈ real typing speed
HUMAN_DELAY_MIN_MS = 300
HUMAN_DELAY_MAX_MS = 3000

# Server start time for uptime tracking
_start_time: float = time.time()


def _human_delay_for_chunk(chunk: str) -> float:
    """Calculate a human-like delay (in seconds) based on chunk length."""
    base = len(chunk) * HUMAN_DELAY_MS_PER_CHAR
    # Add ±30% jitter
    jitter = random.uniform(0.7, 1.3)
    ms = max(HUMAN_DELAY_MIN_MS, min(int(base * jitter), HUMAN_DELAY_MAX_MS))
    return ms / 1000.0


def _chunk_message(text: str, max_chars: int = CHUNK_MAX_CHARS) -> list[str]:
    """Split a long message into natural chunks for sequential sending.

    1. Always split on \\n\\n (paragraph boundary).
    2. If a paragraph <= max_chars, keep it whole.
    3. If a paragraph > max_chars:
       a. Split by sentence-enders (.!?。！？~), group consecutive
          sentences so each chunk stays near max_chars (split roughly
          from the middle, not every sentence boundary).
       b. If a grouped chunk is still > max_chars (single long sentence),
          apply clause-level splitting (，,、：:；;——--) which removes
          the delimiter.
    """
    text = text.strip()
    if not text:
        return []

    # Protect URLs from being split on punctuation (.:?! etc.)
    _URL_PLACEHOLDER = "\x01"
    _url_re = re.compile(r'https?://\S+')
    _urls_found = _url_re.findall(text)
    for _i, _u in enumerate(_urls_found):
        text = text.replace(_u, f"{_URL_PLACEHOLDER}{_i}{_URL_PLACEHOLDER}", 1)

    # Protect file extensions from being split on the dot (case-insensitive)
    _PLACEHOLDER = "\x00"
    _ext_re = re.compile(r'\.(?:md|jpeg|jpg|png|py|js|ts|json|html|css|txt|csv|pdf|zip|gif|svg|mp3|mp4|wav)\b', re.IGNORECASE)
    text = _ext_re.sub(lambda m: _PLACEHOLDER + m.group(0)[1:], text)

    # Step 1: Split on \n\n unconditionally
    paragraphs = re.split(r'\n\n+', text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    # Level-1: sentence-enders (punctuation kept via lookbehind)
    # English period only splits when NOT preceded by a digit (avoids "1. item" or "v2.0")
    _sentence_re = re.compile(
        r'(?<=(?<!\d)[.])'
        r'|(?<=[!?。！？~\n])'
    )
    # Level-2: clause delimiters (consumed = removed)
    _clause_re = re.compile(
        r'[，,、：:；;]'
        r'|'
        r'(?:——|--)'
    )

    def _group_parts(parts: list[str], limit: int) -> list[str]:
        """Greedily group consecutive parts so each chunk <= limit."""
        groups: list[str] = []
        buf = ''
        for p in parts:
            candidate = (buf + p) if buf else p
            if len(candidate) <= limit:
                buf = candidate
            else:
                if buf:
                    groups.append(buf)
                buf = p
        if buf:
            groups.append(buf)
        return groups

    chunks: list[str] = []
    for para in paragraphs:
        if len(para) <= max_chars:
            chunks.append(para)
            continue

        # Level-1: split by sentence-enders, then group
        sentences = [s.strip() for s in _sentence_re.split(para) if s.strip()]
        grouped = _group_parts(sentences, max_chars)

        for chunk in grouped:
            if len(chunk) <= max_chars:
                chunks.append(chunk)
            else:
                # Level-2: clause-level split for overlong single sentence
                clauses = [c.strip() for c in _clause_re.split(chunk) if c.strip()]
                grouped2 = _group_parts(clauses, max_chars)
                chunks.extend(grouped2)

    # Restore protected file extensions and URLs
    result = [c.replace(_PLACEHOLDER, ".") for c in chunks if c]
    for _i, _u in enumerate(_urls_found):
        result = [c.replace(f"{_URL_PLACEHOLDER}{_i}{_URL_PLACEHOLDER}", _u) for c in result]
    return result


def _decide_chunks(
    content: str, split_content: bool, num_chunks: int | None,
) -> list[str]:
    """Decide how to split outgoing message content into chunks.

    Priority:
      1. num_chunks == 1        → single message (no split)
      2. num_chunks >= 2        → punctuation-split then merge into exactly N
      3. </分段> tag in content → split on tag, strip the tag, keep segments as-is
      4. split_content & ≤100   → punctuation split for short messages
      5. default                → single message
    """
    stripped = content.strip()

    if num_chunks is not None and num_chunks == 1:
        return [stripped] if stripped else []

    if num_chunks is not None and num_chunks >= 2 and stripped:
        fine_chunks = _chunk_message(content)
        if len(fine_chunks) <= num_chunks:
            return fine_chunks
        chunks: list[str] = []
        per_group = len(fine_chunks) / num_chunks
        for i in range(num_chunks):
            start = round(i * per_group)
            end = round((i + 1) * per_group)
            chunks.append("\n".join(fine_chunks[start:end]))
        return chunks

    tag_chunks = _split_by_tag(content)
    if tag_chunks is not None:
        return tag_chunks

    if split_content and len(stripped) <= 100:
        return _chunk_message(content)

    return [stripped] if stripped else []


def register_tools(
    mcp: Any, config: Config, bot: OneBotClient, ctx: ContextManager,
    browser_holder: dict | None = None,
) -> None:
    """Register all MCP tools on the FastMCP server instance."""

    @mcp.tool()
    async def check_status() -> dict:
        """Check QQ login status and NapCat connection status."""
        try:
            login_info = await bot.get_login_info()
        except Exception as e:
            return {
                "napcat_running": False,
                "qq_logged_in": False,
                "error": str(e),
            }

        # Online status
        online_status = "unknown"
        try:
            status = await bot.get_status()
            if status.get("online", False):
                online_status = "online"
            else:
                online_status = "offline"
        except Exception:
            pass

        try:
            groups = await bot.get_group_list()
        except Exception:
            groups = []

        monitored_groups = []
        for g in groups:
            gid = str(g.get("group_id", ""))
            if config.is_group_monitored(gid):
                monitored_groups.append(
                    {
                        "group_id": gid,
                        "group_name": g.get("group_name", ""),
                        "member_count": g.get("member_count", 0),
                    }
                )

        # Resolve friend nicknames
        monitored_friends = []
        try:
            all_friends = await bot.get_friend_list()
            if config.friends is None:
                # Monitor all friends
                monitored_friends = [
                    {"user_id": str(f.get("user_id", "")),
                     "nickname": f.get("nickname", f.get("remark", ""))}
                    for f in all_friends
                ]
            else:
                friend_map = {str(f.get("user_id", "")): f for f in all_friends}
                for uid in config.friends:
                    f = friend_map.get(uid, {})
                    monitored_friends.append({
                        "user_id": uid,
                        "nickname": f.get("nickname", f.get("remark", "")),
                    })
        except Exception:
            if config.friends is not None:
                monitored_friends = [{"user_id": uid, "nickname": ""} for uid in config.friends]

        return {
            "napcat_running": True,
            "qq_logged_in": True,
            "qq_account": str(login_info.get("user_id", "")),
            "qq_nickname": login_info.get("nickname", ""),
            "online_status": online_status,
            "uptime_seconds": int(time.time() - _start_time),
            "monitored_groups": monitored_groups,
            "monitored_friends": monitored_friends,
            "total_groups": len(groups),
            "buffer_stats": ctx.buffer_stats,
        }

    @mcp.tool()
    async def get_group_list() -> dict:
        """Get the list of QQ groups the bot has joined."""
        groups = await bot.get_group_list()
        return {
            "groups": [
                {
                    "group_id": str(g.get("group_id", "")),
                    "group_name": g.get("group_name", ""),
                    "member_count": g.get("member_count", 0),
                }
                for g in groups
            ]
        }

    @mcp.tool()
    async def get_friend_list() -> dict:
        """Get the list of QQ friends."""
        friends = await bot.get_friend_list()
        return {
            "friends": [
                {
                    "user_id": str(f.get("user_id", "")),
                    "nickname": f.get("nickname", f.get("remark", "")),
                }
                for f in friends
            ]
        }

    @mcp.tool()
    async def get_recent_context(
        target: str,
        target_type: str = "group",
        limit: int = 200,
    ) -> dict:
        """Get recent message context for a monitored group or whitelisted friend.

        Returns all buffered messages (backfill + real-time) without compression.
        Use compress_context to manually compress when needed.
        Images are returned as URL strings in each message's image_urls field.

        Args:
            target: Group ID or friend QQ ID.
            target_type: "group" (default) or "private".
            limit: Number of recent messages to return (default 200).
        """
        # Whitelist check
        if target_type == "group":
            if not config.is_group_monitored(target):
                return {"error": f"Group {target} is not monitored"}
        elif target_type == "private":
            if not config.is_friend_monitored(target):
                return {"error": f"User {target} is not in friends whitelist"}
        else:
            return {"error": f"Invalid target_type: {target_type}"}

        limit = max(1, limit)

        result = ctx.get_context(target, target_type, limit)

        # Add group_name / friend_name if possible
        if target_type == "group":
            try:
                info = await bot.get_group_info(target)
                result["group_name"] = info.get("group_name", "")
            except Exception:
                result["group_name"] = ""
        else:
            # Enrich friend_name from friend list
            friend_name = ""
            try:
                friends = await bot.get_friend_list()
                for f in friends:
                    if str(f.get("user_id", "")) == target:
                        friend_name = f.get("nickname", f.get("remark", ""))
                        break
            except Exception:
                pass
            result["friend_name"] = friend_name

        return result

    @mcp.tool()
    async def batch_get_recent_context(
        targets: list[dict],
        limit: int = 50,
    ) -> dict:
        """Batch query recent message context for multiple targets.

        More efficient than calling get_recent_context multiple times:
        uses at most 2 OneBot API calls (group list + friend list) regardless
        of how many targets are queried.

        Args:
            targets: List of dicts, each with "target" (ID) and optional
                     "target_type" ("group" or "private", default "group").
                     Example: [{"target": "123", "target_type": "group"},
                               {"target": "456", "target_type": "private"}]
            limit: Number of recent messages per target (default 50).
        """
        limit = max(1, min(limit, 200))

        # Classify targets
        group_ids: list[str] = []
        friend_ids: list[str] = []
        for t in targets:
            tt = t.get("target_type", "group")
            tid = str(t.get("target", ""))
            if tt == "group":
                group_ids.append(tid)
            elif tt == "private":
                friend_ids.append(tid)

        # Batch fetch names — at most 2 API calls total
        group_name_map: dict[str, str] = {}
        if group_ids:
            try:
                all_groups = await bot.get_group_list()
                group_name_map = {
                    str(g.get("group_id", "")): g.get("group_name", "")
                    for g in all_groups
                }
            except Exception as e:
                logger.warning("batch: failed to get group list: %s", e)

        friend_name_map: dict[str, str] = {}
        if friend_ids:
            try:
                all_friends = await bot.get_friend_list()
                friend_name_map = {
                    str(f.get("user_id", "")): f.get("nickname", f.get("remark", ""))
                    for f in all_friends
                }
            except Exception as e:
                logger.warning("batch: failed to get friend list: %s", e)

        # Build results — pure memory reads
        results: list[dict] = []
        for t in targets:
            target = str(t.get("target", ""))
            target_type = t.get("target_type", "group")

            # Whitelist check
            if target_type == "group" and not config.is_group_monitored(target):
                results.append({"target": target, "target_type": target_type,
                                "error": f"Group {target} is not monitored"})
                continue
            if target_type == "private" and not config.is_friend_monitored(target):
                results.append({"target": target, "target_type": target_type,
                                "error": f"User {target} is not in friends whitelist"})
                continue
            if target_type not in ("group", "private"):
                results.append({"target": target, "target_type": target_type,
                                "error": f"Invalid target_type: {target_type}"})
                continue

            # Read from memory buffer
            result = ctx.get_context(target, target_type, limit)

            # Attach name from pre-fetched map (0 API calls)
            if target_type == "group":
                result["group_name"] = group_name_map.get(target, "")
            else:
                result["friend_name"] = friend_name_map.get(target, "")

            results.append(result)

        return {"results": results, "count": len(results)}

    @mcp.tool()
    async def send_message(
        target: str,
        content: str,
        target_type: str = "group",
        reply_to: str | None = None,
        split_content: bool = False,
        num_chunks: int | None = None,
    ) -> dict:
        """Send a message to a monitored group or whitelisted friend.

        Preferred way to send multiple messages: insert `</分段>` in the content
        at each desired split point. Each segment becomes its own message; the
        tag itself is stripped. Example:
            content = "吃了吗</分段>今天忙不忙"
        sends two messages: "吃了吗" and "今天忙不忙". Use this whenever you want
        to split a reply into multiple messages — it is more natural than
        `num_chunks` because you choose the split points yourself.

        Args:
            target: Group ID or friend QQ ID.
            content: Text message content. May contain `</分段>` markers to
                specify exact split points between messages.
            target_type: "group" (default) or "private".
            reply_to: Optional message ID to reply to.
            split_content: If True (and content has no `</分段>` tag), auto-split
                short messages (≤100 chars) on punctuation. Default False.
            num_chunks: Force exactly this many chunks via punctuation-based
                merging. Overrides the `</分段>` tag. Set to 1 to force a single
                message even when the content contains `</分段>`.

        Split-point priority: num_chunks=1 → num_chunks≥2 → `</分段>` tag →
        split_content → single message.
        """
        # Whitelist check
        if target_type == "group":
            if not config.is_group_monitored(target):
                return {"success": False, "error": f"Group {target} is not monitored"}
        elif target_type == "private":
            if not config.is_friend_monitored(target):
                return {
                    "success": False,
                    "error": f"User {target} is not in friends whitelist",
                }
        else:
            return {"success": False, "error": f"Invalid target_type: {target_type}"}

        # Silence check — LLM should not call send_message for [沉默]
        if content.strip().startswith("[沉默]"):
            return {
                "success": False,
                "error": "你发送了沉默，沉默不该调用 MCP 接口。",
            }

        # Rate limit
        now = time.time()
        key = f"{target_type}:{target}"
        last = _last_send.get(key, 0)
        if now - last < RATE_LIMIT_SECONDS:
            wait = RATE_LIMIT_SECONDS - (now - last)
            return {
                "success": False,
                "error": f"Rate limited. Try again in {wait:.1f}s",
            }
        _last_send[key] = now

        # Duplicate detection (before chunking)
        dup_warning = _check_duplicate(key, content)
        if dup_warning:
            return {"success": False, "error": dup_warning}

        chunks = _decide_chunks(content, split_content, num_chunks)
        if not chunks:
            return {"success": False, "error": "Empty message content"}

        sent_ids: list[str] = []
        first_reply_to = reply_to  # Only first chunk is a reply
        t0 = time.time()  # record baseline for incremental message snapshot

        try:
            for i, chunk_text in enumerate(chunks):
                # Strip trailing periods for natural chat style
                chunk_text = chunk_text.rstrip("。.")
                if not chunk_text:
                    continue
                msg = _text_to_segments(chunk_text)
                rto = first_reply_to if i == 0 else None

                if target_type == "group":
                    result = await bot.send_group_msg(target, msg, reply_to=rto)
                else:
                    result = await bot.send_private_msg(target, msg, reply_to=rto)

                msg_id = str(result.get("message_id", ""))
                sent_ids.append(msg_id)

                # Write bot's own message directly into buffer (don't wait for WS echo)
                bot_msg = Message(
                    sender_id=config.qq,
                    sender_name="bot",
                    content=chunk_text,
                    timestamp=datetime.now(CST).isoformat(),
                    message_id=msg_id,
                    is_self=True,
                )
                ctx.add_message(target, target_type, bot_msg)


        except Exception as e:
            _last_send[key] = last  # rollback rate limit on failure
            if sent_ids:
                return {
                    "success": False,
                    "error": f"Partial send ({len(sent_ids)}/{len(chunks)} chunks): {e}",
                    "message_ids": sent_ids,
                }
            return {"success": False, "error": str(e)}

        # Snapshot: all messages since this send_message started (incremental)
        recent_msgs = ctx.get_messages_since(target, target_type, t0)
        recent_lines: list[str] = []
        for m in recent_msgs:
            if m.is_self:
                tag = "[bot(self)]"
            else:
                tag = f"[{m.sender_name}]"
            recent_lines.append(f"{tag} {m.content}")

        return {
            "success": True,
            "message_ids": sent_ids,
            "chunks": len(chunks),
            "target": target,
            "target_type": target_type,
            "timestamp": datetime.now(CST).isoformat(),
            "recent_messages": recent_lines,
        }

    @mcp.tool()
    async def send_image(
        target: str,
        image: str,
        target_type: str = "group",
        reply_to: str | None = None,
    ) -> dict:
        """Send an image to a monitored group or whitelisted friend.

        Args:
            target: Group ID or friend QQ ID.
            image: Base64-encoded image data (without the base64:// prefix).
            target_type: "group" (default) or "private".
            reply_to: Optional message ID to reply to.
        """
        # Whitelist check
        if target_type == "group":
            if not config.is_group_monitored(target):
                return {"success": False, "error": f"Group {target} is not monitored"}
        elif target_type == "private":
            if not config.is_friend_monitored(target):
                return {
                    "success": False,
                    "error": f"User {target} is not in friends whitelist",
                }
        else:
            return {"success": False, "error": f"Invalid target_type: {target_type}"}

        # Rate limit
        now = time.time()
        key = f"{target_type}:{target}"
        last = _last_send.get(key, 0)
        if now - last < RATE_LIMIT_SECONDS:
            wait = RATE_LIMIT_SECONDS - (now - last)
            return {
                "success": False,
                "error": f"Rate limited. Try again in {wait:.1f}s",
            }
        _last_send[key] = now

        msg = [{"type": "image", "data": {"file": f"base64://{image}"}}]

        try:
            if target_type == "group":
                result = await bot.send_group_msg(target, msg, reply_to=reply_to)
            else:
                result = await bot.send_private_msg(target, msg, reply_to=reply_to)
        except Exception as e:
            _last_send[key] = last  # rollback rate limit on failure
            return {"success": False, "error": str(e)}

        msg_id = str(result.get("message_id", ""))

        # Write bot's own message into buffer
        bot_msg = Message(
            sender_id=config.qq,
            sender_name="bot",
            content="[图片]",
            timestamp=datetime.now(CST).isoformat(),
            message_id=msg_id,
            is_self=True,
        )
        ctx.add_message(target, target_type, bot_msg)

        return {
            "success": True,
            "message_id": msg_id,
            "target": target,
            "target_type": target_type,
            "timestamp": datetime.now(CST).isoformat(),
        }

    @mcp.tool()
    async def send_voice(
        target: str,
        audio: str,
        target_type: str = "group",
    ) -> dict:
        """Send a voice message to a monitored group or whitelisted friend.

        NapCat auto-converts common formats (MP3/WAV/AMR/OGG/FLAC) to SILK.

        Args:
            target: Group ID or friend QQ ID.
            audio: Base64-encoded audio data (without the base64:// prefix).
            target_type: "group" (default) or "private".
        """
        # Whitelist check
        if target_type == "group":
            if not config.is_group_monitored(target):
                return {"success": False, "error": f"Group {target} is not monitored"}
        elif target_type == "private":
            if not config.is_friend_monitored(target):
                return {
                    "success": False,
                    "error": f"User {target} is not in friends whitelist",
                }
        else:
            return {"success": False, "error": f"Invalid target_type: {target_type}"}

        msg = [{"type": "record", "data": {"file": f"base64://{audio}"}}]

        try:
            if target_type == "group":
                result = await bot.send_group_msg(target, msg)
            else:
                result = await bot.send_private_msg(target, msg)
        except Exception as e:
            return {"success": False, "error": str(e)}

        msg_id = str(result.get("message_id", ""))

        # Write bot's own message into buffer
        bot_msg = Message(
            sender_id=config.qq,
            sender_name="bot",
            content="[语音]",
            timestamp=datetime.now(CST).isoformat(),
            message_id=msg_id,
            is_self=True,
        )
        ctx.add_message(target, target_type, bot_msg)

        return {
            "success": True,
            "message_id": msg_id,
            "target": target,
            "target_type": target_type,
            "timestamp": datetime.now(CST).isoformat(),
        }

    @mcp.tool()
    async def compress_context(
        target: str,
        ctx_mcp: Context,
        target_type: str = "group",
    ) -> dict:
        """Compress all buffered messages for a target into a summary.

        This replaces raw messages with a compressed summary, freeing up the buffer.
        Use this after reading context when you want to archive old messages.

        Args:
            target: Group ID or friend QQ ID.
            target_type: "group" (default) or "private".
        """
        # Whitelist check
        if target_type == "group":
            if not config.is_group_monitored(target):
                return {"error": f"Group {target} is not monitored"}
        elif target_type == "private":
            if not config.is_friend_monitored(target):
                return {"error": f"User {target} is not in friends whitelist"}
        else:
            return {"error": f"Invalid target_type: {target_type}"}

        buf_key = ctx._buffer_key(target_type, target)
        buf = ctx._buffers.get(buf_key)
        if buf is None or len(buf.messages) == 0:
            return {
                "success": True,
                "compressed": 0,
                "message": "No messages to compress",
                "compressed_summary": buf.compressed_summary if buf else None,
            }

        # Extract all messages
        all_msgs = list(buf.messages)
        buf.messages.clear()
        buf._compress_pending = False
        buf._compress_all_pending = False
        buf._msg_since_compress = 0

        # Try LLM compression, fall back to rule-based
        try:
            summary = await _llm_compress(ctx_mcp, all_msgs)
            method = "llm"
        except Exception as e:
            logger.warning("LLM compression failed, using rule-based: %s", e)
            summary = _rule_based_compress(all_msgs)
            method = "rule-based"

        buf.apply_summary(summary)
        logger.info("%s compressed %d messages for %s", method, len(all_msgs), buf_key)

        return {
            "success": True,
            "compressed": len(all_msgs),
            "method": method,
            "compressed_summary": buf.compressed_summary,
        }

    @mcp.tool()
    async def screenshot_chat(
        target: str,
        message_id: str,
        target_type: str = "group",
    ) -> dict:
        """Take a QQ-style screenshot of chat messages starting from a specific message.

        Renders messages as a dark-mode QQ chat screenshot (iPhone style) and
        returns a base64-encoded PNG image.

        The screenshot starts from the given message_id and renders downward.
        If the messages fit on one screen, earlier messages are prepended to
        fill the screen (bottom-aligned). If they overflow, later messages
        are cut off at the bottom.

        Args:
            target: Group ID or friend QQ ID.
            message_id: The message ID to start rendering from.
            target_type: "group" (default) or "private".
        """
        from .renderer import render_to_base64, measure_chat_height

        if target_type == "group":
            if not config.is_group_monitored(target):
                return {"success": False, "error": f"Group {target} is not monitored"}
        elif target_type == "private":
            if not config.is_friend_monitored(target):
                return {"success": False, "error": f"User {target} is not in friends whitelist"}
        else:
            return {"success": False, "error": f"Invalid target_type: {target_type}"}

        # Get browser (lazy-start Playwright)
        if browser_holder is None:
            return {"success": False, "error": "Screenshot not available (no browser)"}

        if browser_holder["browser"] is None:
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            browser_holder["pw"] = pw
            browser_holder["browser"] = await pw.chromium.launch()
            logger.info("Playwright browser started")

        browser = browser_holder["browser"]

        # Find message_id in buffer and split into before/after
        buf_key = ctx._buffer_key(target_type, target)
        buf = ctx._buffers.get(buf_key)
        if buf is None:
            return {"success": False, "error": f"No messages buffered for {target}"}

        all_msgs = list(buf.messages)
        # Find the index of message_id
        start_idx = None
        for i, m in enumerate(all_msgs):
            if m.message_id == message_id:
                start_idx = i
                break

        if start_idx is None:
            return {"success": False, "error": f"Message {message_id} not found in buffer"}

        after_msgs = all_msgs[start_idx:]  # message_id and everything after
        before_msgs = all_msgs[:start_idx]  # everything before message_id

        # Get group info
        group_name = target
        member_count = 0
        if target_type == "group":
            try:
                info = await bot.get_group_info(target)
                group_name = info.get("group_name", target)
                member_count = info.get("member_count", 0)
            except Exception:
                pass

        # Get member info (title/level/role) for all senders
        sender_ids = set(m.sender_id for m in all_msgs)
        member_info: dict[str, dict] = {}
        for uid in sender_ids:
            try:
                mi = await bot._call("get_group_member_info",
                                     group_id=int(target), user_id=int(uid))
                if mi:
                    member_info[uid] = mi
            except Exception:
                pass

        def _msg_to_dict(m: Message) -> dict:
            mi = member_info.get(m.sender_id, {})
            return {
                "sender_id": m.sender_id,
                "sender_name": m.sender_name,
                "content": m.content,
                "timestamp": m.timestamp,
                "message_id": m.message_id,
                "is_at_me": m.is_at_me,
                "is_self": m.is_self,
                "image_urls": m.image_urls,
                "_title": mi.get("title", ""),
                "_role": mi.get("role", "member"),
                "_level": mi.get("level", ""),
            }

        after_dicts = [_msg_to_dict(m) for m in after_msgs]

        # Measure if after_msgs fit on screen
        chat_scroll, chat_client = await measure_chat_height(
            browser, after_dicts, group_name, member_count,
        )

        if chat_scroll <= chat_client:
            # Fits — prepend earlier messages to fill the screen
            combined = [_msg_to_dict(m) for m in before_msgs] + after_dicts
            # Keep adding earlier msgs until it overflows, then use bottom_align
            b64 = await render_to_base64(
                browser, combined, group_name, member_count,
                bottom_align=True,
            )
        else:
            # Overflow — render from message_id, top-aligned
            b64 = await render_to_base64(
                browser, after_dicts, group_name, member_count,
                bottom_align=False,
            )

        return {
            "success": True,
            "image": b64,
            "message_count": len(after_dicts),
            "target": target,
            "target_type": target_type,
            "start_message_id": message_id,
        }


async def _llm_compress(ctx_mcp: Context, messages: list) -> str:
    """Use the client's LLM (via MCP sampling) to compress messages into a summary."""
    # Format messages for the LLM
    lines = []
    for m in messages:
        lines.append(f"[{m.timestamp}] {m.sender_name}: {m.content}")
    chat_log = "\n".join(lines)

    result = await ctx_mcp.session.create_message(
        messages=[
            SamplingMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=(
                        "请将以下聊天记录压缩为一段简洁的中文摘要，保留关键信息（话题、观点、重要发言者）。"
                        "摘要应在 300 字以内，不要使用列表格式，用自然段落描述。\n\n"
                        f"聊天记录：\n{chat_log}"
                    ),
                ),
            )
        ],
        max_tokens=8192,
        system_prompt="你是一个聊天记录摘要助手。只输出摘要内容，不要添加任何前缀或解释。",
    )

    # Extract text from result
    if hasattr(result, "content"):
        content = result.content
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, TextContent):
            return content.text.strip()
        if isinstance(content, list):
            parts = []
            for c in content:
                if hasattr(c, "text"):
                    parts.append(c.text)
            return " ".join(parts).strip()
    return str(result).strip()


def _rule_based_compress(messages: list) -> str:
    """Fallback: rule-based compression when LLM is unavailable."""
    lines = []
    for m in messages:
        content = m.content[:80] + "..." if len(m.content) > 80 else m.content
        lines.append(f"{m.sender_name}: {content}")
    summary_block = " | ".join(lines)
    ts_range = f"[{messages[0].timestamp} ~ {messages[-1].timestamp}]"
    return f"{ts_range} {summary_block}"
