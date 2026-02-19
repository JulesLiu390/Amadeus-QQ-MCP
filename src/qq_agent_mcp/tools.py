"""MCP Tools definitions."""

import asyncio
import logging
import random
import re
import time
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
RATE_LIMIT_SECONDS = 3.0
CST = timezone(timedelta(hours=8))

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

    # Restore protected file extensions
    return [c.replace(_PLACEHOLDER, ".") for c in chunks if c]


def register_tools(
    mcp: Any, config: Config, bot: OneBotClient, ctx: ContextManager
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
        if config.friends:
            try:
                all_friends = await bot.get_friend_list()
                friend_map = {str(f.get("user_id", "")): f for f in all_friends}
                for uid in config.friends:
                    f = friend_map.get(uid, {})
                    monitored_friends.append({
                        "user_id": uid,
                        "nickname": f.get("nickname", f.get("remark", "")),
                    })
            except Exception:
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
        split_content: bool = True,
    ) -> dict:
        """Send a message to a monitored group or whitelisted friend.

        Args:
            target: Group ID or friend QQ ID.
            content: Text message content.
            target_type: "group" (default) or "private".
            reply_to: Optional message ID to reply to.
            split_content: Whether to split long messages into multiple chunks
                with typing delay (default True). Set to False to send as a
                single message without splitting.
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

        # Split long messages into chunks (or send as one)
        if split_content:
            chunks = _chunk_message(content)
        else:
            chunks = [content.strip()] if content.strip() else []
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
                msg = [{"type": "text", "data": {"text": chunk_text}}]
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

                # Human-like delay based on chunk length (not after last)
                if i < len(chunks) - 1:
                    delay = _human_delay_for_chunk(chunk_text)
                    await asyncio.sleep(delay)

        except Exception as e:
            _last_send[key] = last  # rollback rate limit on failure
            if sent_ids:
                return {
                    "success": False,
                    "error": f"Partial send ({len(sent_ids)}/{len(chunks)} chunks): {e}",
                    "message_ids": sent_ids,
                }
            return {"success": False, "error": str(e)}

        # Brief wait for WebSocket to deliver group reactions
        await asyncio.sleep(0.5)

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
