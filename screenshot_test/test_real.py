#!/usr/bin/env python3
"""Test screenshot rendering with real messages from NapCat Docker."""

import asyncio
import json
import os
import sys

import aiohttp

# Reuse rendering logic from test_render
from test_render import render_screenshot, CST

from datetime import datetime, timedelta

ONEBOT_URL = "http://127.0.0.1:3000"
BOT_QQ = "3825478002"


async def api_call(session: aiohttp.ClientSession, action: str, **params) -> dict:
    async with session.post(f"{ONEBOT_URL}/{action}", json=params) as resp:
        data = await resp.json()
        if data.get("retcode") != 0:
            print(f"API error: {action} -> {data}")
            return {}
        return data.get("data", {})


def parse_message_segments(segments: list[dict], bot_qq: str) -> tuple[str, list[str], bool]:
    """Parse OneBot message segments into text content, image URLs, and is_at_me flag."""
    parts = []
    image_urls = []
    is_at_me = False

    for seg in segments:
        seg_type = seg.get("type", "")
        data = seg.get("data", {})

        if seg_type == "text":
            parts.append(data.get("text", ""))
        elif seg_type == "image":
            url = data.get("url", "")
            if url:
                image_urls.append(url)
            # Don't add "[图片]" text — the image itself will be rendered
        elif seg_type == "face":
            face_id = str(data.get("id", "?"))
            # Pass face text for super faces (faceType 2)
            raw = data.get("raw", {})
            face_text = raw.get("faceText", "") if isinstance(raw, dict) else ""
            parts.append(f"[QQ_FACE:{face_id}:{face_text}]")
        elif seg_type == "at":
            qq = str(data.get("qq", ""))
            if qq == bot_qq:
                is_at_me = True
            parts.append(f"@{qq}")
        elif seg_type == "reply":
            # Will be handled separately
            pass
        else:
            parts.append(f"[{seg_type}]")

    return "".join(parts), image_urls, is_at_me


async def fetch_reply_preview(session: aiohttp.ClientSession, reply_id: str) -> str | None:
    """Fetch the original message being replied to and return a short preview."""
    try:
        data = await api_call(session, "get_msg", message_id=int(reply_id))
        if not data:
            return None
        sender = data.get("sender", {})
        name = sender.get("card") or sender.get("nickname", "?")
        segs = data.get("message", [])
        text_parts = []
        for seg in segs:
            if seg.get("type") == "text":
                text_parts.append(seg["data"].get("text", ""))
            elif seg.get("type") == "image":
                text_parts.append("[图片]")
        preview = "".join(text_parts)[:30]
        return f"[回复了 {name} 的「{preview}」]"
    except Exception:
        return None


async def fetch_and_render(group_id: int, count: int = 8):
    async with aiohttp.ClientSession() as session:
        # Fetch group info
        group_info = await api_call(session, "get_group_info", group_id=group_id)
        group_name = group_info.get("group_name", str(group_id))
        member_count = group_info.get("member_count", 0)

        # Fetch messages
        data = await api_call(session, "get_group_msg_history", group_id=group_id, count=count)
        raw_msgs = data.get("messages", [])
        print(f"Fetched {len(raw_msgs)} messages from '{group_name}'")

        # Fetch member info for all unique senders (title, level, role)
        sender_ids = set(str(e.get("sender", {}).get("user_id", "")) for e in raw_msgs)
        member_info: dict[str, dict] = {}
        for uid in sender_ids:
            if not uid:
                continue
            try:
                info = await api_call(session, "get_group_member_info",
                                      group_id=group_id, user_id=int(uid))
                if info:
                    member_info[uid] = info
            except Exception:
                pass

        messages = []
        for event in raw_msgs:
            sender = event.get("sender", {})
            sender_id = str(sender.get("user_id", ""))
            sender_name = sender.get("card") or sender.get("nickname", "?")
            segments = event.get("message", [])
            timestamp = event.get("time", 0)
            message_id = str(event.get("message_id", ""))
            is_self = sender_id == BOT_QQ

            # Check for reply segment
            reply_prefix = ""
            for seg in segments:
                if seg.get("type") == "reply":
                    reply_id = seg["data"].get("id", "")
                    preview = await fetch_reply_preview(session, reply_id)
                    if preview:
                        reply_prefix = preview + " "
                    break

            content, image_urls, is_at_me = parse_message_segments(segments, BOT_QQ)
            content = reply_prefix + content

            # Convert timestamp
            dt = datetime.fromtimestamp(timestamp, tz=CST)

            # Member info: title, level, role
            mi = member_info.get(sender_id, {})
            title = mi.get("title", "")
            level = mi.get("level", "")
            role = mi.get("role", "member")

            messages.append({
                "sender_id": sender_id,
                "sender_name": sender_name,
                "content": content,
                "timestamp": dt.isoformat(),
                "message_id": message_id,
                "is_at_me": is_at_me,
                "is_self": is_self,
                "image_urls": image_urls,
                "_title": title,
                "_role": role,
                "_level": level,
            })

        if not messages:
            print("No messages to render!")
            return

        print(f"Rendering {len(messages)} messages...")
        for m in messages:
            tag = "[self]" if m["is_self"] else f"[{m['sender_name']}]"
            print(f"  {tag} {m['content'][:60]}...")

        await render_screenshot(
            messages,
            group_name=group_name,
            member_count=member_count,
            output_path="chat_real.png",
        )


async def main():
    group_id = int(sys.argv[1]) if len(sys.argv) > 1 else 902317662
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    await fetch_and_render(group_id, count)
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
