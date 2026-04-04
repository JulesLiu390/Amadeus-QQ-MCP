#!/usr/bin/env python3
"""Standalone test: render sample QQ chat messages as a screenshot (QQ dark mode style)."""

import asyncio
import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from jinja2 import Template
from markupsafe import Markup
from playwright.async_api import async_playwright

CST = timezone(timedelta(hours=8))

# ── QQ Face ID → Emoji mapping (common ones) ──────────────
_FACE_EMOJI = {
    "0": "\U0001f62e", "1": "\U0001f623", "2": "\u2764\ufe0f",
    "4": "\U0001f60e", "5": "\U0001f62d", "6": "\u263a\ufe0f",
    "7": "\U0001f636", "8": "\U0001f634", "9": "\U0001f62d",
    "10": "\U0001f633", "11": "\U0001f621", "12": "\U0001f61c",
    "13": "\U0001f601", "14": "\U0001f642", "15": "\U0001f641",
    "16": "\U0001f60e", "18": "\U0001f622", "19": "\U0001f44d",
    "21": "\U0001f60f", "23": "\U0001f616", "24": "\U0001f637",
    "25": "\U0001f44f", "27": "\U0001f914", "28": "\U0001f910",
    "29": "\U0001f631", "32": "\U0001f914", "33": "\U0001f624",
    "34": "\U0001f624", "46": "\U0001f437", "53": "\U0001f382",
    "55": "\U0001f4a3", "59": "\U0001f4a9", "60": "\u2615",
    "63": "\U0001f339", "66": "\u2764\ufe0f", "74": "\u2600\ufe0f",
    "75": "\U0001f319", "76": "\U0001f44d", "77": "\U0001f44e",
    "78": "\U0001f91d", "79": "\u270c\ufe0f", "85": "\U0001f4a3",
    "86": "\U0001f620", "96": "\U0001f622", "97": "\U0001f613",
    "100": "\U0001f614", "101": "\U0001f628", "104": "\U0001f62d",
    "109": "\U0001f48b", "110": "\U0001f628", "111": "\U0001f4a2",
    "146": "\U0001f382", "147": "\U0001f60d", "171": "\U0001f4b5",
    "172": "\U0001f604", "175": "\U0001f602", "176": "\U0001f60e",
    "178": "\U0001f44d", "179": "\U0001f91d", "180": "\U0001f60a",
    "182": "\U0001f60b", "183": "\U0001f60f", "212": "\U0001f60a",
    "277": "\U0001f929", "320": "\U0001f970",
}

# Stable color palette for avatar fallback
_AVATAR_COLORS = [
    "#e74c3c", "#e67e22", "#f1c40f", "#2ecc71", "#1abc9c",
    "#3498db", "#9b59b6", "#e84393", "#00b894", "#6c5ce7",
]

# Badge colors by role
_BADGE_STYLES = {
    "群主": "#9b59b6",
    "管理员": "#3498db",
    "会员": "#e67e22",
    "活跃": "#2ecc71",
}

# QQ group default level tiers (gaming rank style)
# QQ group 6-tier system — level ranges are group-specific
# Based on observed data: NOW(LV14)=白银, Orzjh(LV3)=青铜
_LEVEL_TIERS = [
    (1,  "青铜", "#666"),
    (7,  "白银", "#666"),
    (19, "黄金", "#666"),
    (31, "铂金", "#666"),
    (49, "钻石", "#666"),
    (67, "王者", "#666"),
]


def _level_to_tier(level: str) -> tuple[str, str] | None:
    """Convert numeric level to tier name and color."""
    if not level or not level.isdigit():
        return None
    lv = int(level)
    if lv < 1:
        return None
    result = _LEVEL_TIERS[0]
    for threshold, name, color in _LEVEL_TIERS:
        if lv >= threshold:
            result = (name, color)
        else:
            break
    return result[0], result[1]


# ── Sample messages (closer to real QQ group) ─────────────

SAMPLE_MESSAGES = [
    {
        "sender_id": "1234567890",
        "sender_name": "Amadeus",
        "content": "Gemini 3 Flash高分但token成本也最贵，DeepSeek V3反而性价比挺能打的，1.61元那行真的香。",
        "timestamp": "2026-04-03T17:30:00+08:00",
        "message_id": "2001",
        "is_at_me": False,
        "is_self": False,
        "image_urls": [],
        "_badge": "管理员",
    },
    {
        "sender_id": "2345678901",
        "sender_name": "葵葵",
        "content": "ds搞机器人表现怎么样",
        "timestamp": "2026-04-03T17:32:00+08:00",
        "message_id": "2002",
        "is_at_me": False,
        "is_self": False,
        "image_urls": [],
        "_badge": "活跃",
    },
    {
        "sender_id": "3456789012",
        "sender_name": "Glitch",
        "content": "[回复了 葵葵(2345678901) 的「ds搞机器人表现怎么样」] @2345678901 DS适合做逻辑大脑，但具身需要极速视觉对齐。原生多模态的Gemini反馈更稳，是控制首选",
        "timestamp": "2026-04-03T17:33:00+08:00",
        "message_id": "2003",
        "is_at_me": False,
        "is_self": False,
        "image_urls": [],
        "_badge": "会员",
    },
    {
        "sender_id": "1234567890",
        "sender_name": "Amadeus",
        "content": "DeepSeek在推理能力上确实不错，但具身机器人更吃实时多模态和低延迟，这块它先天不足，拿来做任务规划层还行，感知控制层就勉强了。",
        "timestamp": "2026-04-03T17:35:00+08:00",
        "message_id": "2004",
        "is_at_me": False,
        "is_self": False,
        "image_urls": [],
        "_badge": "管理员",
    },
    {
        "sender_id": "4567890123",
        "sender_name": "bot",
        "content": "总结一下大家的讨论：\n1. Gemini Flash 性能强但成本高\n2. DeepSeek V3 性价比好\n3. 具身机器人需要低延迟多模态",
        "timestamp": "2026-04-03T17:36:00+08:00",
        "message_id": "2005",
        "is_at_me": False,
        "is_self": True,
        "image_urls": [],
    },
    # Gap > 5 min
    {
        "sender_id": "2345678901",
        "sender_name": "葵葵",
        "content": "有没有人试过这个 https://github.com/NapNeko/NapCatQQ 搭QQ机器人",
        "timestamp": "2026-04-03T17:50:00+08:00",
        "message_id": "2006",
        "is_at_me": False,
        "is_self": False,
        "image_urls": [],
        "_badge": "活跃",
    },
    {
        "sender_id": "4567890123",
        "sender_name": "bot",
        "content": "[回复了 葵葵(2345678901) 的「有没有人试过这个」] 我就是用NapCat跑的 [CQ:face,id=175]",
        "timestamp": "2026-04-03T17:50:30+08:00",
        "message_id": "2007",
        "is_at_me": False,
        "is_self": True,
        "image_urls": [],
    },
    {
        "sender_id": "3456789012",
        "sender_name": "Glitch",
        "content": "@4567890123 你是什么模型驱动的？",
        "timestamp": "2026-04-03T17:51:00+08:00",
        "message_id": "2008",
        "is_at_me": True,
        "is_self": False,
        "image_urls": [],
        "_badge": "会员",
    },
    {
        "sender_id": "4567890123",
        "sender_name": "bot",
        "content": "Claude Opus [CQ:face,id=178]",
        "timestamp": "2026-04-03T17:51:30+08:00",
        "message_id": "2009",
        "is_at_me": False,
        "is_self": True,
        "image_urls": [],
    },
]

GROUP_NAME = "ACM AI/Agent/LLM Dev"
MEMBER_COUNT = 842


# ── Prepare messages for template ──────────────────────────

def parse_timestamp(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def format_time(dt: datetime) -> str:
    now = datetime.now(CST)
    if dt.date() == now.date():
        hour = dt.hour
        if hour < 6:
            period = "凌晨"
        elif hour < 12:
            period = "上午"
        elif hour == 12:
            period = "中午"
        elif hour < 18:
            period = "下午"
        else:
            period = "晚上"
        return f"{period}{dt.strftime('%H:%M')}"
    elif dt.date() == (now - timedelta(days=1)).date():
        return "昨天 " + dt.strftime("%H:%M")
    else:
        return dt.strftime("%m月%d日 %H:%M")


def _avatar_color(sender_id: str) -> str:
    idx = int(hashlib.md5(sender_id.encode()).hexdigest(), 16) % len(_AVATAR_COLORS)
    return _AVATAR_COLORS[idx]


_FACES_DIR = os.path.join(os.path.dirname(__file__), "faces")

# Pre-load all face images as base64 data URIs (317 faces, ~5MB in memory)
_FACE_B64_CACHE: dict[str, str] = {}


def _load_face_cache():
    import base64 as _b64
    if _FACE_B64_CACHE:
        return
    if not os.path.isdir(_FACES_DIR):
        return
    for fname in os.listdir(_FACES_DIR):
        if fname.endswith(".png"):
            fid = fname[:-4]  # "424.png" → "424"
            path = os.path.join(_FACES_DIR, fname)
            with open(path, "rb") as f:
                _FACE_B64_CACHE[fid] = _b64.b64encode(f.read()).decode()


def _face_to_html(face_id: str, face_text: str = "") -> str:
    """Convert a QQ face ID to an inline HTML element."""
    _load_face_cache()
    # 1. Local face image (128x128 PNG from QFace CDN)
    b64 = _FACE_B64_CACHE.get(face_id)
    if b64:
        return f'<img class="qq-face" src="data:image/png;base64,{b64}" />'
    # 2. Emoji mapping fallback
    emoji = _FACE_EMOJI.get(face_id)
    if emoji:
        return emoji
    # 3. Text fallback
    label = face_text.strip("/") if face_text else f"表情{face_id}"
    return f"[{label}]"


def _clean_content(content: str, name_map: dict[str, str]) -> str:
    # QQ_FACE marker from real data parser → inline face image/emoji
    def _qq_face_repl(m):
        fid = m.group(1)
        face_text = m.group(2) if m.group(2) else ""
        return _face_to_html(fid, face_text)
    content = re.sub(r"\[QQ_FACE:(\d+):([^\]]*)\]", _qq_face_repl, content)

    # CQ face → inline face image/emoji
    def _cq_face_repl(m):
        fid = m.group(1)
        return _face_to_html(fid)
    content = re.sub(r"\[CQ:face,id=(\d+)\]", _cq_face_repl, content)
    content = re.sub(r"\[表情(\d+)\]", _cq_face_repl, content)

    # @QQ号 → @昵称
    def _at_repl(m):
        qq = m.group(1)
        name = name_map.get(qq, qq)
        return f"@{name}"
    content = re.sub(r"@(\d{5,11})", _at_repl, content)

    return content


def _parse_reply(content: str) -> tuple[str | None, str | None, str | None, str]:
    """Parse reply prefix from content.

    Returns (reply_sender, reply_time, reply_content, remaining_content).
    Pattern: [回复了 Name(QQ) 的「text」] rest
    """
    if not content.startswith("[回复了"):
        return None, None, None, content

    bracket_end = content.find("]")
    if bracket_end < 0:
        return None, None, None, content

    reply_raw = content[1:bracket_end]  # "回复了 Name(QQ) 的「text」"
    remaining = content[bracket_end + 1:].strip()

    # Extract sender name and quoted text
    m = re.match(r"回复了\s+(.+?)(?:\(\d+\))?\s*的「(.+?)」", reply_raw)
    if m:
        return m.group(1), None, m.group(2), remaining

    return None, None, reply_raw, remaining


def prepare_messages(raw_messages: list[dict], time_gap_minutes: int = 10) -> list[dict]:
    # Build name map
    name_map = {}
    for msg in raw_messages:
        name_map[msg["sender_id"]] = msg["sender_name"]

    result = []
    last_time = None

    for msg in raw_messages:
        ts = parse_timestamp(msg["timestamp"])

        if last_time is None or (ts - last_time).total_seconds() > time_gap_minutes * 60:
            result.append({"type": "time", "text": format_time(ts)})
        last_time = ts

        content = msg["content"]
        reply_sender, reply_time, reply_content, content = _parse_reply(content)

        # Clean CQ codes and @QQ号
        content = _clean_content(content, name_map)
        if reply_content:
            reply_content = _clean_content(reply_content, name_map)

        # Replace newlines with <br>, then mark as safe HTML (for face images)
        content = Markup(content.replace("\n", "<br>"))
        if reply_content:
            reply_content = Markup(reply_content)

        # Badge: owner title (yellow), admin title (green), member title (purple)
        title = msg.get("_title", "")
        role = msg.get("_role", "member")
        if title and role == "owner":
            badge = title
            badge_color = "#d4a017"  # yellow
        elif title and role == "admin":
            badge = title
            badge_color = "#6bb8a0"  # teal green
        elif title:
            badge = title
            badge_color = "#9b59b6"  # purple
        elif role == "owner":
            badge = "群主"
            badge_color = "#d4a017"  # yellow/gold
        elif role == "admin":
            badge = "管理员"
            badge_color = "#6bb8a0"  # teal green
        else:
            badge = None
            badge_color = None

        # Level tier — only show if no title and not admin/owner
        level = msg.get("_level", "")
        if title or role in ("owner", "admin"):
            tier_name = ""
            tier_color = ""
        else:
            tier = _level_to_tier(level)
            tier_name = tier[0] if tier else ""
            tier_color = tier[1] if tier else ""

        result.append({
            "type": "msg",
            "sender_id": msg["sender_id"],
            "sender_name": msg["sender_name"],
            "content": content,
            "is_self": msg.get("is_self", False),
            "is_at_me": msg.get("is_at_me", False),
            "reply_text": reply_content if reply_sender else None,
            "reply_sender": reply_sender,
            "reply_time": reply_time,
            "reply_content": reply_content,
            "image_urls": msg.get("image_urls", []),
            "img_only": bool(msg.get("image_urls")) and not str(content).strip(),
            "avatar_color": _avatar_color(msg["sender_id"]),
            "badge": badge,
            "badge_color": badge_color,
            "level_name": tier_name,
            "level_color": tier_color,
        })

    return result


# ── Render and screenshot ──────────────────────────────────

async def render_screenshot(
    messages: list[dict],
    group_name: str,
    member_count: int = 0,
    unread: int = 3,
    output_path: str = "chat_screenshot.png",
    width: int = 480,
):
    # iPhone-like aspect ratio: 9:19.5 → height = width * 19.5/9
    height = int(width * 19.5 / 9)

    template_path = os.path.join(os.path.dirname(__file__), "chat_template.html")
    with open(template_path, "r", encoding="utf-8") as f:
        template = Template(f.read())

    # Dock image — embed as base64 data URI for reliable rendering
    import base64
    dock_image_path = os.path.join(os.path.dirname(__file__), "images", "qq-dock.png")
    with open(dock_image_path, "rb") as f:
        dock_b64 = base64.b64encode(f.read()).decode()
    dock_image_url = f"data:image/png;base64,{dock_b64}"

    # Status bar time
    now = datetime.now(CST)
    status_time = now.strftime("%H:%M")

    prepared = prepare_messages(messages)

    html = template.render(
        group_name=group_name,
        member_count=member_count,
        unread=unread,
        status_time=status_time,
        dock_image=dock_image_url,
        message_count=len([m for m in prepared if m["type"] == "msg"]),
        messages=prepared,
    )

    # Update body dimensions in rendered HTML
    html = html.replace("width: 480px;", f"width: {width}px;")
    html = html.replace("height: 1040px;", f"height: {height}px;")

    html_path = os.path.join(os.path.dirname(__file__), "chat_debug.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML saved to: {html_path}")
    print(f"Viewport: {width}x{height} (iPhone ratio)")

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(
            viewport={"width": width, "height": height},
            device_scale_factor=3,
        )
        await page.set_content(html, wait_until="networkidle")
        await page.wait_for_timeout(2000)

        output = os.path.join(os.path.dirname(__file__), output_path)
        await page.screenshot(path=output, clip={"x": 0, "y": 0, "width": width, "height": height})
        await browser.close()

    file_size = os.path.getsize(output) / 1024
    print(f"Screenshot saved to: {output} ({file_size:.1f} KB)")


async def main():
    print(f"Rendering {len(SAMPLE_MESSAGES)} messages from '{GROUP_NAME}'...")
    await render_screenshot(SAMPLE_MESSAGES, GROUP_NAME, MEMBER_COUNT)
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
