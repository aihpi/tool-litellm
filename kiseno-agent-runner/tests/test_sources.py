import httpx
import pytest

from app.sources import KubernetesConfigMapSource
from app.spec import AGENT_LABEL, SKILL_LABEL


@pytest.mark.asyncio
async def test_lists_agent_and_skill_configmaps_with_the_service_account_token():
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.url.params.get("labelSelector"), request.headers.get("authorization")))
        if request.url.params.get("labelSelector") == AGENT_LABEL:
            items = [{"metadata": {"labels": {AGENT_LABEL: "helper"}}, "data": {"agent.yaml": "apiVersion: kisz.ai/v1\nname: helper\ndescription: x\nskills: [notes]\n"}}]
        else:
            items = [{"metadata": {"labels": {SKILL_LABEL: "notes"}}, "data": {"SKILL.md": "---\nname: notes\ndescription: n\n---\nbody"}}]
        return httpx.Response(200, json={"items": items})

    source = KubernetesConfigMapSource(
        "kisz-agents", api_url="https://k8s.test", token="sa-token", transport=httpx.MockTransport(handler), verify=False
    )
    catalog = await source.load()

    assert list(catalog.agents) == ["helper"] and catalog.agents["helper"].skills[0].body == "body"
    assert seen == [
        ("/api/v1/namespaces/kisz-agents/configmaps", AGENT_LABEL, "Bearer sa-token"),
        ("/api/v1/namespaces/kisz-agents/configmaps", SKILL_LABEL, "Bearer sa-token"),
    ]


@pytest.mark.asyncio
async def test_api_errors_surface_instead_of_emptying_the_catalog():
    """An empty catalog would make the sync delete every agent, so a failed list must raise."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden"})

    source = KubernetesConfigMapSource(
        "kisz-agents", api_url="https://k8s.test", token="t", transport=httpx.MockTransport(handler), verify=False
    )
    with pytest.raises(httpx.HTTPStatusError):
        await source.load()
