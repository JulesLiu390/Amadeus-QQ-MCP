"""QQ chat screenshot renderer — renders messages as a QQ dark mode style image."""

import base64
import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from jinja2 import Template
from markupsafe import Markup

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))

_PKG_DIR = os.path.dirname(__file__)
_FACES_DIR = os.path.join(_PKG_DIR, "assets", "faces")
_DOCK_PATH = os.path.join(_PKG_DIR, "assets", "images", "qq-dock.png")
_TEMPLATE_PATH = os.path.join(_PKG_DIR, "templates", "chat.html")

# ── Face emoji fallback mapping ──────────────────────────────
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

_AVATAR_COLORS = [
    "#e74c3c", "#e67e22", "#f1c40f", "#2ecc71", "#1abc9c",
    "#3498db", "#9b59b6", "#e84393", "#00b894", "#6c5ce7",
]

_LEVEL_TIERS = [
    (1,  "青铜", "#666"),
    (7,  "白银", "#666"),
    (19, "黄金", "#666"),
    (31, "铂金", "#666"),
    (49, "钻石", "#666"),
    (67, "王者", "#666"),
]

# ── Caches (loaded once per process) ─────────────────────────
_FACE_B64_CACHE: dict[str, str] = {}
_DOCK_B64: str = ""
_TEMPLATE: Template | None = None


_loaded = False


def _ensure_loaded():
    """Lazy-load face images, dock image, and template."""
    global _DOCK_B64, _TEMPLATE, _loaded
    if _loaded:
        return
    _loaded = True
    if os.path.isdir(_FACES_DIR):
        for fname in os.listdir(_FACES_DIR):
            if fname.endswith(".png"):
                fid = fname[:-4]
                with open(os.path.join(_FACES_DIR, fname), "rb") as f:
                    _FACE_B64_CACHE[fid] = base64.b64encode(f.read()).decode()
        logger.info("Loaded %d face images", len(_FACE_B64_CACHE))
    if os.path.isfile(_DOCK_PATH):
        with open(_DOCK_PATH, "rb") as f:
            _DOCK_B64 = base64.b64encode(f.read()).decode()
        logger.info("Dock image loaded")
    if os.path.isfile(_TEMPLATE_PATH):
        with open(_TEMPLATE_PATH, "r", encoding="utf-8") as f:
            _TEMPLATE = Template(f.read())
        logger.info("Chat template loaded")


# ── Helpers ──────────────────────────────────────────────────

def _level_to_tier(level: str) -> tuple[str, str] | None:
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


def _avatar_color(sender_id: str) -> str:
    idx = int(hashlib.md5(sender_id.encode()).hexdigest(), 16) % len(_AVATAR_COLORS)
    return _AVATAR_COLORS[idx]


def _face_to_html(face_id: str, face_text: str = "") -> str:
    _ensure_loaded()
    b64 = _FACE_B64_CACHE.get(face_id)
    if b64:
        return f'<img class="qq-face" src="data:image/png;base64,{b64}" />'
    emoji = _FACE_EMOJI.get(face_id)
    if emoji:
        return emoji
    label = face_text.strip("/") if face_text else f"表情{face_id}"
    return f"[{label}]"


def _clean_content(content: str, name_map: dict[str, str]) -> str:
    def _qq_face_repl(m):
        return _face_to_html(m.group(1), m.group(2) if m.group(2) else "")
    content = re.sub(r"\[QQ_FACE:(\d+):([^\]]*)\]", _qq_face_repl, content)

    def _cq_face_repl(m):
        return _face_to_html(m.group(1))
    content = re.sub(r"\[CQ:face,id=(\d+)\]", _cq_face_repl, content)
    content = re.sub(r"\[表情(\d+)\]", _cq_face_repl, content)

    def _at_repl(m):
        return f"@{name_map.get(m.group(1), m.group(1))}"
    content = re.sub(r"@(\d{5,11})", _at_repl, content)

    return content


def _parse_reply(content: str) -> tuple[str | None, str | None, str | None, str]:
    if not content.startswith("[回复了"):
        return None, None, None, content
    bracket_end = content.find("]")
    if bracket_end < 0:
        return None, None, None, content
    reply_raw = content[1:bracket_end]
    remaining = content[bracket_end + 1:].strip()
    m = re.match(r"回复了\s+(.+?)(?:\(\d+\))?\s*的「(.+?)」", reply_raw)
    if m:
        return m.group(1), None, m.group(2), remaining
    return None, None, reply_raw, remaining


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


# ── Prepare messages for template ────────────────────────────

def prepare_messages(raw_messages: list[dict], time_gap_minutes: int = 10) -> list[dict]:
    name_map = {msg["sender_id"]: msg["sender_name"] for msg in raw_messages}
    result = []
    last_time = None

    for msg in raw_messages:
        ts = parse_timestamp(msg["timestamp"])
        if last_time is None or (ts - last_time).total_seconds() > time_gap_minutes * 60:
            result.append({"type": "time", "text": format_time(ts)})
        last_time = ts

        content = msg["content"]
        # Strip "[图片]" text when actual images are present (avoid duplicate)
        if msg.get("image_urls"):
            content = content.replace("[图片]", "").strip()
        reply_sender, reply_time, reply_content, content = _parse_reply(content)

        content = _clean_content(content, name_map)
        if reply_content:
            reply_content = _clean_content(reply_content, name_map)

        content = Markup(content.replace("\n", "<br>"))
        if reply_content:
            reply_content = Markup(reply_content)

        # Badge
        title = msg.get("_title", "")
        role = msg.get("_role", "member")
        if title and role == "owner":
            badge, badge_color = title, "#d4a017"
        elif title and role == "admin":
            badge, badge_color = title, "#6bb8a0"
        elif title:
            badge, badge_color = title, "#9b59b6"
        elif role == "owner":
            badge, badge_color = "群主", "#d4a017"
        elif role == "admin":
            badge, badge_color = "管理员", "#6bb8a0"
        else:
            badge, badge_color = None, None

        # Level tier
        level = msg.get("_level", "")
        if title or role in ("owner", "admin"):
            tier_name, tier_color = "", ""
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


# ── Render to base64 PNG ─────────────────────────────────────

WIDTH = 480
HEIGHT = int(WIDTH * 19.5 / 9)  # iPhone ratio


async def render_to_base64(
    browser,
    messages: list[dict],
    group_name: str,
    member_count: int = 0,
    unread: int = 3,
    bottom_align: bool = False,
) -> str:
    """Render messages to a QQ-style screenshot and return base64 PNG."""
    _ensure_loaded()

    prepared = prepare_messages(messages)
    status_time = datetime.now(CST).strftime("%H:%M")
    dock_image_url = f"data:image/png;base64,{_DOCK_B64}" if _DOCK_B64 else ""

    html = _TEMPLATE.render(
        group_name=group_name,
        member_count=member_count,
        unread=unread,
        status_time=status_time,
        dock_image=dock_image_url,
        message_count=len([m for m in prepared if m["type"] == "msg"]),
        messages=prepared,
        bottom_align=bottom_align,
    )

    html = html.replace("width: 480px;", f"width: {WIDTH}px;")
    html = html.replace("height: 1040px;", f"height: {HEIGHT}px;")

    page = await browser.new_page(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=3,
    )
    try:
        await page.set_content(html, wait_until="networkidle")
        await page.wait_for_timeout(1500)
        png_bytes = await page.screenshot(
            clip={"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT}
        )
    finally:
        await page.close()

    return base64.b64encode(png_bytes).decode()


async def measure_chat_height(browser, messages, group_name, member_count, unread=3) -> int:
    """Render messages and return the natural chat content height in px."""
    _ensure_loaded()

    prepared = prepare_messages(messages)
    status_time = datetime.now(CST).strftime("%H:%M")
    dock_image_url = f"data:image/png;base64,{_DOCK_B64}" if _DOCK_B64 else ""

    html = _TEMPLATE.render(
        group_name=group_name,
        member_count=member_count,
        unread=unread,
        status_time=status_time,
        dock_image=dock_image_url,
        message_count=len([m for m in prepared if m["type"] == "msg"]),
        messages=prepared,
        bottom_align=False,
    )

    html = html.replace("width: 480px;", f"width: {WIDTH}px;")
    html = html.replace("height: 1040px;", f"height: {HEIGHT}px;")

    page = await browser.new_page(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=1,
    )
    try:
        await page.set_content(html, wait_until="networkidle")
        chat_scroll = await page.evaluate("document.querySelector('.chat').scrollHeight")
        chat_client = await page.evaluate("document.querySelector('.chat').clientHeight")
    finally:
        await page.close()

    return chat_scroll, chat_client
