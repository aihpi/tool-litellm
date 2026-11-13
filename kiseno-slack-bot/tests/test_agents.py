from __future__ import annotations

import json

import httpx
import pytest
from app.agents import (
    AgentConfigError,
    AgentStore,
    KubernetesConfigMapSource,
    agents_from_configmaps,
    parse_agent_yaml,
    refresh_agents,
)

QWEN_YAML = """
apiVersion: kiseno/v1
name: qwen-bot
description: The team's Qwen agent
model: qwen3-8-27b
max_turns: 12
teams: [team-internal]
mcp_access_groups: [qwen-bot]
skills: [workshop-pflegen]
skills_loading: all
tools: [espocrm_find]
future_field: {anything: goes}
slack:
  default: true
  channels: [CWORKFLOWS]
  listen: mentions
  dm: true
"""


def configmap(name: str, text: str | None) -> dict:
    data = {} if text is None else {"agent.yaml": text}
    return {"metadata": {"name": name, "labels": {"kiseno/agent": name}}, "data": data}


def test_parse_reads_name_teams_and_slack_and_ignores_the_rest():
    agent = parse_agent_yaml(QWEN_YAML)
    assert agent.name == "qwen-bot"
    assert agent.teams == ("team-internal",)
    assert agent.slack.default is True
    assert agent.slack.channels == ("CWORKFLOWS",)
    assert agent.slack.listen == "mentions"
    assert agent.slack.dm is True


def test_parse_defaults_without_slack_block():
    agent = parse_agent_yaml("apiVersion: kiseno/v1\nname: helper\n")
    assert agent.teams == ()
    assert agent.slack.default is False
    assert agent.slack.listen == "mentions"
    assert agent.slack.dm is False


@pytest.mark.parametrize(
    "text",
    [
        "apiVersion: kiseno/v2\nname: x\n",
        "apiVersion: kiseno/v1\n",
        "apiVersion: kiseno/v1\nname: x\nslack: {listen: sometimes}\n",
        "apiVersion: kiseno/v1\nname: x\nteams: team-internal\n",
        "apiVersion: kiseno/v1\nname: x\nslack: {dm: 'yes'}\n",
        "- not a mapping\n",
        "name: [unclosed\n",
    ],
)
def test_parse_rejects_invalid_definitions(text):
    with pytest.raises(AgentConfigError):
        parse_agent_yaml(text)


def test_configmaps_skip_invalid_missing_and_duplicate_agents():
    agents = agents_from_configmaps(
        [
            configmap("qwen-bot", QWEN_YAML),
            configmap("broken", "apiVersion: kiseno/v1\n"),
            configmap("empty", None),
            configmap("qwen-bot-copy", QWEN_YAML),
        ]
    )
    assert [a.name for a in agents] == ["qwen-bot"]


def test_store_finds_default_and_channel_agents():
    store = AgentStore(
        agents_from_configmaps(
            [
                configmap("qwen-bot", QWEN_YAML),
                configmap(
                    "protokoll",
                    "apiVersion: kiseno/v1\nname: protokoll\nslack: {channels: [CPROTO], listen: all}\n",
                ),
            ]
        )
    )
    assert store.default_agent().name == "qwen-bot"
    assert store.agent_for_channel("CPROTO").name == "protokoll"
    assert store.agent_for_channel("CWORKFLOWS").name == "qwen-bot"
    assert store.agent_for_channel("COTHER") is None


@pytest.mark.asyncio
async def test_kubernetes_source_lists_labelled_configmaps_with_the_service_account_token(tmp_path):
    token = tmp_path / "token"
    token.write_text("sa-token\n")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["selector"] = request.url.params["labelSelector"]
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, content=json.dumps({"items": [configmap("qwen-bot", QWEN_YAML)]}))

    source = KubernetesConfigMapSource(
        "kiseno-agents",
        api_url="https://k8s.test",
        token_path=token,
        ca_path=tmp_path / "ca.crt",
        transport=httpx.MockTransport(handler),
    )
    agents = await source.list_agents()

    assert [a.name for a in agents] == ["qwen-bot"]
    assert seen == {
        "path": "/api/v1/namespaces/kiseno-agents/configmaps",
        "selector": "kiseno/agent",
        "auth": "Bearer sa-token",
    }


@pytest.mark.asyncio
async def test_refresh_keeps_the_last_agents_when_the_api_fails(tmp_path):
    token = tmp_path / "token"
    token.write_text("sa-token")
    responses = [
        httpx.Response(200, json={"items": [configmap("qwen-bot", QWEN_YAML)]}),
        httpx.Response(403, json={"message": "forbidden"}),
        httpx.Response(200, json={"items": []}),
    ]
    source = KubernetesConfigMapSource(
        "kiseno-agents",
        api_url="https://k8s.test",
        token_path=token,
        transport=httpx.MockTransport(lambda _: responses.pop(0)),
    )
    store = AgentStore()

    assert await refresh_agents(source, store) is True
    assert [a.name for a in store.all()] == ["qwen-bot"]
    assert await refresh_agents(source, store) is False
    assert [a.name for a in store.all()] == ["qwen-bot"]
    assert await refresh_agents(source, store) is True
    assert store.all() == []
