"""MCP Server main entry — registers tools, manages lifecycle."""

import asyncio
import logging
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from .config import Config
from .context import ContextManager
from .onebot import OneBotClient
from .tools import register_tools

logger = logging.getLogger(__name__)

MAX_READY_WAIT = 30  # seconds to wait for NapCat to be reachable


async def _wait_ready(bot: OneBotClient, timeout: float = MAX_READY_WAIT) -> bool:
    """Poll OneBot /get_login_info until reachable or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    attempt = 0
    while asyncio.get_event_loop().time() < deadline:
        attempt += 1
        try:
            info = await bot.get_login_info()
            logger.info(
                "NapCat ready (QQ: %s, nickname: %s) after %d attempts",
                info.get("user_id"), info.get("nickname"), attempt,
            )
            return True
        except Exception as e:
            logger.debug("wait_ready attempt %d: %s", attempt, e)
            await asyncio.sleep(min(2.0, deadline - asyncio.get_event_loop().time()))
    logger.warning("NapCat not reachable after %.0fs — starting anyway", timeout)
    return False


def create_server(config: Config) -> FastMCP:
    """Create and configure the MCP Server."""
    bot = OneBotClient(config.onebot_base_url)
    ctx = ContextManager(config)

    @asynccontextmanager
    async def lifespan(app: FastMCP):
        # Startup: check NapCat is reachable, then backfill + start WS
        await _wait_ready(bot)
        await ctx.backfill_history(bot)
        ctx.start()
        logger.info("Context manager started (WS: %s)", config.ws_url)
        try:
            yield {}
        finally:
            # Shutdown: stop WebSocket listener and close HTTP client
            await ctx.stop()
            await bot.close()
            logger.info("Context manager and bot client stopped")

    mcp = FastMCP("qq-agent-mcp", lifespan=lifespan)

    # Register all MCP tools
    register_tools(mcp, config, bot, ctx)

    return mcp


def run_server(config: Config) -> None:
    """Start the MCP Server with stdio transport (blocking)."""
    mcp = create_server(config)
    logger.info("Starting MCP Server (QQ: %s, OneBot: %s)", config.qq, config.onebot_base_url)
    mcp.run(transport="stdio")
