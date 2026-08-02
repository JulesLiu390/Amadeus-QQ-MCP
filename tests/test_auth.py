import asyncio

from qq_agent_mcp.config import Config
from qq_agent_mcp.onebot import OneBotClient


def test_config_builds_bearer_header_only_when_token_is_present():
    assert Config(qq="10001").auth_headers == {}
    assert Config(qq="10001", access_token="secret").auth_headers == {
        "Authorization": "Bearer secret"
    }


def test_onebot_client_keeps_access_token_for_http_requests():
    client = OneBotClient("http://127.0.0.1:3000", access_token="secret")
    assert client.access_token == "secret"


def test_onebot_http_request_sends_bearer_header():
    calls = []

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def json(self):
            return {"retcode": 0, "data": {"online": True}}

    class Session:
        def post(self, url, json, headers):
            calls.append({"url": url, "json": json, "headers": headers})
            return Response()

        async def close(self):
            return None

    client = OneBotClient("http://127.0.0.1:3000", access_token="secret")
    client._session = Session()
    result = asyncio.run(client._call("get_status"))

    assert result == {"online": True}
    assert calls[0]["headers"] == {"Authorization": "Bearer secret"}
