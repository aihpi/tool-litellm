import httpx
import pytest

from app.litellm_client import LiteLLMClient, LiteLLMNotFoundError


@pytest.mark.asyncio
async def test_ensure_user_creates_when_user_is_missing():
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url), request.content.decode()))
        if request.method == "GET":
            return httpx.Response(404, json={"detail": "User not found"})
        return httpx.Response(200, json={"user_id": "abc"})

    client = LiteLLMClient(
        "http://litellm.test",
        "sk-test",
        transport=httpx.MockTransport(handler),
    )

    response = await client.ensure_user(
        user_id="abc",
        user_email="user@example.com",
        user_role="internal_user",
        max_budget=10.0,
    )

    assert response["user_id"] == "abc"
    assert len(calls) == 2
    assert calls[0][0] == "GET"
    assert calls[1][0] == "POST"
    assert "/user/new" in calls[1][1]


@pytest.mark.asyncio
async def test_generate_key_posts_expected_payload():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"key": "sk-generated"})

    client = LiteLLMClient(
        "http://litellm.test",
        "sk-test",
        transport=httpx.MockTransport(handler),
    )

    response = await client.generate_key(
        user_id="abc",
        key_alias="demo",
        max_budget=5.0,
    )

    assert response["key"] == "sk-generated"
    assert captured["method"] == "POST"
    assert "/key/generate" in captured["url"]
    assert '"user_id":"abc"' in captured["body"]
    assert '"key_alias":"demo"' in captured["body"]
    assert '"max_budget":5.0' in captured["body"]


@pytest.mark.asyncio
async def test_get_user_info_raises_not_found_for_404():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "User missing"})

    client = LiteLLMClient(
        "http://litellm.test",
        "sk-test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(LiteLLMNotFoundError):
        await client.get_user_info("missing")
