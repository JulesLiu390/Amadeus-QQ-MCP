"""CLI entry point for qq-agent-mcp."""

import argparse
import logging
import sys

from .config import Config
from .server import run_server


def parse_args() -> Config:
    parser = argparse.ArgumentParser(
        prog="qq-agent-mcp",
        description="MCP Server for QQ via NapCatQQ (OneBot v11)",
    )
    parser.add_argument("--qq", required=True, help="QQ account number")
    parser.add_argument(
        "--napcat-host",
        default="127.0.0.1",
        help="NapCat OneBot HTTP host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--napcat-port",
        type=int,
        default=3000,
        help="NapCat OneBot HTTP port (default: 3000)",
    )
    parser.add_argument(
        "--ws-port",
        type=int,
        default=3001,
        help="NapCat WebSocket port (default: 3001)",
    )
    parser.add_argument(
        "--groups",
        default=None,
        help="Comma-separated group IDs to monitor (default: all)",
    )
    parser.add_argument(
        "--friends",
        default=None,
        help="Comma-separated friend QQ IDs to monitor private chats (default: none)",
    )
    parser.add_argument(
        "--buffer-size",
        type=int,
        default=100,
        help="Message buffer size per target (default: 100)",
    )
    parser.add_argument(
        "--compress-every",
        type=int,
        default=30,
        help="Compress old messages every N new messages (default: 30)",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Log level (default: info)",
    )

    args = parser.parse_args()

    groups: set[str] | None = None
    if args.groups:
        groups = set(args.groups.split(","))

    friends: set[str] = set()
    if args.friends:
        friends = set(args.friends.split(","))

    return Config(
        qq=args.qq,
        napcat_host=args.napcat_host,
        napcat_port=args.napcat_port,
        ws_port=args.ws_port,
        groups=groups,
        friends=friends,
        buffer_size=args.buffer_size,
        compress_every=args.compress_every,
        log_level=args.log_level,
    )


def main() -> None:
    config = parse_args()

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    run_server(config)


if __name__ == "__main__":
    main()
