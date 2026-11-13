import httpx
import pytest

from app.litellm_client import Caller, LiteLLMGateway, McpTool
from app.loop import LOAD_SKILL, LoopConfig, ToolTable, run_agent
from app.spec import build_catalog
from fakes import FakeLiteLLM, mcp_tool, text_reply, tool_reply

SKILL = "---\nname: crm\ndescription: CRM work.\ntools: [\"crm*\"]\n---\n\nUse the CRM tools.\n"


def agent(extra: str = "", skills: bool = True):
    yaml = "apiVersion: kiseno/v1\nname: helper\ndescription: Helps.\n" + ("skills: [crm]\n" if skills else "") + extra
    catalog = build_catalog([("helper", {"agent.yaml": yaml, "instructions.md": "Be brief."})], [("crm", SKILL)] if skills else [])
    assert not catalog.invalid, catalog.invalid
    return catalog.agents["helper"]


async def run(fake: FakeLiteLLM, definition, max_turns: int = 5):
    statuses: list[str] = []

    async def on_status(text: str) -> None:
        statuses.append(text)

    client = httpx.AsyncClient(base_url="http://litellm.test", transport=fake.transport())
    answer = await run_agent(
        definition,
        agent_key="sk-agent",
        caller=Caller(user_id="u-1", team_id="t-1", trace_id="tr-1"),
        user_text="Enter the RAG workshop",
        gateway=LiteLLMGateway(client, llm_timeout=10),
        config=LoopConfig(model="qwen3-8-27b", max_turns=max_turns, tool_result_max_chars=50),
        on_status=on_status,
    )
    return answer, statuses


@pytest.mark.asyncio
async def test_tool_call_then_answer_with_caller_headers():
    fake = FakeLiteLLM(tools=[mcp_tool("create_event"), mcp_tool("list", server="calendar")])
    fake.chat_script = [tool_reply(("crm-create_event", {"title": "RAG"})), text_reply("Done: created RAG.")]
    answer, statuses = await run(fake, agent())

    assert answer == "Done: created RAG."
    assert statuses == ["Calling crm-create_event"]
    (call,) = fake.calls_to("POST", "/mcp-rest/tools/call")
    assert call.body == {"name": "create_event", "arguments": {"title": "RAG"}, "server_id": "id-crm"}
    for c in fake.calls:  # model, tool list and tool call all run as the agent, capped at the caller
        assert c.headers["authorization"] == "Bearer sk-agent"
        assert c.headers["x-litellm-user-id"] == "u-1"
        assert c.headers["x-litellm-team-id"] == "t-1"
    second = fake.calls_to("POST", "/v1/chat/completions")[1].body["messages"]
    assert second[-1] == {"role": "tool", "tool_call_id": "call_0", "content": "ran create_event"}


@pytest.mark.asyncio
async def test_all_mode_puts_skills_in_prompt_and_offers_every_tool():
    fake = FakeLiteLLM(tools=[mcp_tool("create_event"), mcp_tool("list", server="calendar")])
    fake.chat_script = [text_reply("hi")]
    await run(fake, agent())
    body = fake.calls_to("POST", "/v1/chat/completions")[0].body
    assert "Use the CRM tools." in body["messages"][0]["content"]
    assert {t["function"]["name"] for t in body["tools"]} == {"crm-create_event", "calendar-list"}


@pytest.mark.asyncio
async def test_on_demand_hides_skill_tools_until_loaded():
    fake = FakeLiteLLM(tools=[mcp_tool("create_event"), mcp_tool("list", server="calendar")])
    seen_tools: list[set[str]] = []

    def capture(reply):
        def step(body):
            seen_tools.append({t["function"]["name"] for t in body.get("tools", [])})
            return reply

        return step

    fake.chat_script = [
        capture(tool_reply((LOAD_SKILL, {"name": "crm"}))),
        capture(tool_reply(("crm-create_event", {"title": "RAG"}))),
        capture(text_reply("ok")),
    ]
    answer, statuses = await run(fake, agent("skills_loading: on_demand\n"))

    assert answer == "ok"
    assert seen_tools[0] == {"calendar-list", LOAD_SKILL}
    assert seen_tools[1] == {"calendar-list", "crm-create_event"}
    assert statuses == ["Loading skill crm", "Calling crm-create_event"]
    first_prompt = fake.calls_to("POST", "/v1/chat/completions")[0].body["messages"][0]["content"]
    assert "Use the CRM tools." not in first_prompt and "- crm: CRM work." in first_prompt


@pytest.mark.asyncio
async def test_allowlist_and_unavailable_tool():
    fake = FakeLiteLLM(tools=[mcp_tool("create_event"), mcp_tool("list", server="calendar")])
    fake.chat_script = [tool_reply(("calendar-list", {})), text_reply("sorry")]
    answer, _ = await run(fake, agent('tools: ["crm-*"]\n', skills=False))
    assert answer == "sorry"
    assert fake.calls_to("POST", "/mcp-rest/tools/call") == []
    tool_msg = fake.calls_to("POST", "/v1/chat/completions")[1].body["messages"][-1]
    assert "not available" in tool_msg["content"]


@pytest.mark.asyncio
async def test_out_of_turns_forces_a_final_answer_and_truncates_results():
    fake = FakeLiteLLM(tools=[mcp_tool("create_event")])
    fake.tool_results["create_event"] = {"content": [{"type": "text", "text": "x" * 500}]}
    fake.chat_script = [tool_reply(("crm-create_event", {})), tool_reply(("crm-create_event", {})), text_reply("partial")]
    answer, statuses = await run(fake, agent(skills=False), max_turns=2)
    assert answer == "partial"
    assert statuses[-1] == "Writing the answer"
    last = fake.calls_to("POST", "/v1/chat/completions")[-1].body
    assert "tools" not in last
    tool_results = [m for m in last["messages"] if m["role"] == "tool"]
    assert all("[truncated" in m["content"] for m in tool_results)


def test_tool_names_are_made_openai_safe_and_unique():
    tools = [McpTool("crm/create event", "", {}, None), McpTool("crm_create_event", "", {}, None), McpTool("x" * 80, "", {}, None)]
    table = ToolTable.build(tools)
    names = list(table.by_function)
    assert names[0] == "crm_create_event" and names[1] == "crm_create_event_2"
    assert len(names[2]) == 64
    assert table.by_function["crm_create_event"].name == "crm/create event"


def test_qualified_names_are_not_prefixed_twice():
    assert McpTool("Find_CEvents", "", {}, "espocrm", "id-1").qualified_name == "espocrm-Find_CEvents"
    assert McpTool("espocrm-Find_CEvents", "", {}, "espocrm", "id-1").qualified_name == "espocrm-Find_CEvents"
    assert McpTool("Find_CEvents", "", {}, None).qualified_name == "Find_CEvents"


@pytest.mark.asyncio
async def test_list_tools_reads_server_from_mcp_info_and_calls_route_by_it():
    fake = FakeLiteLLM(tools=[
        {"name": "Find_CEvents", "description": "", "inputSchema": {"type": "object"}, "mcp_info": {"server_name": "espocrm", "server_id": "uuid-espo"}},
        {"name": "lookup", "description": "", "inputSchema": {"type": "object"}, "mcp_info": {"alias": "cal", "server_id": "uuid-cal"}},
    ])
    gateway = LiteLLMGateway(httpx.AsyncClient(base_url="http://litellm.test", transport=fake.transport()), llm_timeout=10)
    caller = Caller(user_id="u-1")
    tools = await gateway.list_tools("sk-agent", caller)
    assert [(t.qualified_name, t.server_id) for t in tools] == [("espocrm-Find_CEvents", "uuid-espo"), ("cal-lookup", "uuid-cal")]
    result = await gateway.call_tool("sk-agent", caller, tools[0], {"q": "RAG"})
    assert result["content"][0]["text"] == "ran Find_CEvents"
    assert fake.calls_to("POST", "/mcp-rest/tools/call")[0].body == {"name": "Find_CEvents", "arguments": {"q": "RAG"}, "server_id": "uuid-espo"}


@pytest.mark.asyncio
async def test_skill_patterns_match_server_qualified_names():
    """A workshop skill lists "espocrm*"; LiteLLM lists the tool as "Find_CEvents" on server espocrm."""
    fake = FakeLiteLLM(tools=[
        {"name": "Find_CEvents", "description": "", "inputSchema": {"type": "object"}, "mcp_info": {"server_name": "espocrm", "server_id": "uuid-espo"}},
        mcp_tool("list", server="calendar"),
    ])
    fake.chat_script = [text_reply("ok")]
    skill = "---\nname: crm\ndescription: CRM work.\ntools: [\"espocrm*\"]\n---\n\nUse EspoCRM.\n"
    catalog = build_catalog([("helper", {"agent.yaml": "apiVersion: kiseno/v1\nname: helper\ndescription: x\nskills: [crm]\nskills_loading: on_demand\n"})], [("crm", skill)])
    await run(fake, catalog.agents["helper"])
    offered = {t["function"]["name"] for t in fake.calls_to("POST", "/v1/chat/completions")[0].body["tools"]}
    assert offered == {"calendar-list", LOAD_SKILL}  # espocrm-Find_CEvents waits for load_skill
