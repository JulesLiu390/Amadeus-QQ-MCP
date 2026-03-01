"""OneBot v11 HTTP API async client."""

import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


class OneBotError(Exception):
    """Raised when OneBot API returns a non-zero retcode."""

    def __init__(self, action: str, retcode: int, message: str):
        self.action = action
        self.retcode = retcode
        super().__init__(f"OneBot {action} failed (retcode={retcode}): {message}")


class OneBotClient:
    """Async client for NapCat's OneBot v11 HTTP API."""

    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def _call(self, action: str, **params: Any) -> Any:
        """Call an OneBot API action and return the data field."""
        session = await self._ensure_session()

        url = f"{self.base_url}/{action}"
        payload = {k: v for k, v in params.items() if v is not None}

        logger.debug("OneBot call: %s %s", action, payload)

        async with session.post(url, json=payload) as resp:
            result = await resp.json()

        retcode = result.get("retcode", -1)
        if retcode != 0:
            raise OneBotError(
                action, retcode, result.get("message", result.get("wording", ""))
            )

        return result.get("data")

    # ── Query APIs ──────────────────────────────────────────

    async def get_login_info(self) -> dict:
        """Get bot account info. Returns {user_id, nickname}."""
        return await self._call("get_login_info")

    async def get_group_list(self) -> list[dict]:
        """Get joined group list."""
        return await self._call("get_group_list")

    async def get_group_info(self, group_id: str) -> dict:
        """Get info for a specific group."""
        return await self._call("get_group_info", group_id=int(group_id))

    async def get_friend_list(self) -> list[dict]:
        """Get friend list."""
        return await self._call("get_friend_list")

    async def get_status(self) -> dict:
        """Get bot online status."""
        return await self._call("get_status")

    # ── Send APIs ───────────────────────────────────────────

    async def send_group_msg(
        self, group_id: str, message: list[dict], reply_to: str | None = None
    ) -> dict:
        """Send a group message. Returns {message_id}."""
        segments = []
        if reply_to:
            segments.append({"type": "reply", "data": {"id": reply_to}})
        segments.extend(message)
        return await self._call(
            "send_group_msg", group_id=int(group_id), message=segments
        )

    async def send_private_msg(
        self, user_id: str, message: list[dict], reply_to: str | None = None
    ) -> dict:
        """Send a private message. Returns {message_id}."""
        segments = []
        if reply_to:
            segments.append({"type": "reply", "data": {"id": reply_to}})
        segments.extend(message)
        return await self._call(
            "send_private_msg", user_id=int(user_id), message=segments
        )

    async def get_group_msg_history(
        self, group_id: str, count: int = 20
    ) -> list[dict]:
        """Fetch recent group message history. Returns list of message events."""
        data = await self._call(
            "get_group_msg_history", group_id=int(group_id), count=count
        )
        return data.get("messages", []) if data else []

    async def get_friend_msg_history(
        self, user_id: str, count: int = 20
    ) -> list[dict]:
        """Fetch recent private message history (NapCat extension API).

        Returns list of message events, same format as get_group_msg_history.
        """
        data = await self._call(
            "get_friend_msg_history", user_id=int(user_id), count=count
        )
        return data.get("messages", []) if data else []

    async def get_msg(self, message_id: str) -> dict:
        """Fetch a single message by its ID.

        Returns the full message event dict (sender, message, time, etc.).
        """
        return await self._call("get_msg", message_id=int(message_id))

    async def get_forward_msg(self, id: str) -> list[dict]:
        """Fetch forwarded message content by forward ID.

        Returns a list of message nodes, each with sender info and content.
        """
        data = await self._call("get_forward_msg", id=id)
        return data.get("messages", data.get("message", [])) if data else []
