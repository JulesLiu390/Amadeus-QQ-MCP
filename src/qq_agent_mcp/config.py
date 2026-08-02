"""Configuration dataclass for qq-agent-mcp."""

from dataclasses import dataclass, field


@dataclass
class Config:
    qq: str
    napcat_host: str = "127.0.0.1"
    napcat_port: int = 3000
    ws_port: int = 3001
    access_token: str | None = None
    groups: set[str] | None = None  # None = monitor all groups
    friends: set[str] | None = None  # None = monitor all private chats
    buffer_size: int = 100
    compress_every: int = 30
    log_level: str = "info"

    @property
    def onebot_base_url(self) -> str:
        return f"http://{self.napcat_host}:{self.napcat_port}"

    @property
    def ws_url(self) -> str:
        return f"ws://{self.napcat_host}:{self.ws_port}"

    @property
    def auth_headers(self) -> dict[str, str]:
        """Authentication headers shared by OneBot HTTP and WebSocket clients."""
        if not self.access_token:
            return {}
        return {"Authorization": f"Bearer {self.access_token}"}

    def is_group_monitored(self, group_id: str) -> bool:
        """Check if a group is in the monitor list. None means all."""
        if self.groups is None:
            return True
        return group_id in self.groups

    def is_friend_monitored(self, user_id: str) -> bool:
        """Check if a friend is in the whitelist. None means all."""
        if self.friends is None:
            return True
        return user_id in self.friends
