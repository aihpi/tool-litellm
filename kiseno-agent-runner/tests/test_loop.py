import httpx
import pytest

from app.litellm_client import Caller, LiteLLMGateway, McpTool
from app.loop import LOAD_SKILL, LoopConfig, ToolTable, run_agent
from app.spec import build_catalog
from fakes import FakeLiteLLM, mcp_tool, text_reply, tool_reply

SKILL = "---\nname: crm\ndescription: CRM work.\ntools: [\"espocrm*\"]\n---\n\nUse the CRM tools.\n"


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
    fake = FakeLiteLLM(tools=[mcp_tool("espocrm-create_event"), mcp_tool("calendar-list", server="cal")])
    fake.chat_script = [tool_reply(("espocrm-create_event", {"title": "RAG"})), text_reply("Done: created RAG.")]
    answer, statuses = await run(fake, agent())

    assert answer == "Done: created RAG."
    assert statuses == ["Calling espocrm-create_event"]
    (call,) = fake.calls_to("POST", "/mcp-rest/tools/call")
    assert call.body == {"name": "espocrm-create_event", "arguments": {"title": "RAG"}}
    for c in fake.calls:  # model, tool list and tool call all run as the agent, capped at the caller
        assert c.headers["authorization"] == "Bearer sk-agent"
        assert c.headers["x-litellm-user-id"] == "u-1"
        assert c.headers["x-litellm-team-id"] == "t-1"
    second = fake.calls_to("POST", "/v1/chat/completions")[1].body["messages"]
    assert second[-1] == {"role": "tool", "tool_call_id": "call_0", "content": "ran espocrm-create_event"}


@pytest.mark.asyncio
async def test_all_mode_puts_skills_in_prompt_and_offers_every_tool():
    fake = FakeLiteLLM(tools=[mcp_tool("espocrm-create_event"), mcp_tool("calendar-list", server="cal")])
    fake.chat_script = [text_reply("hi")]
    await run(fake, agent())
    body = fake.calls_to("POST", "/v1/chat/completions")[0].body
    assert "Use the CRM tools." in body["messages"][0]["content"]
    assert {t["function"]["name"] for t in body["tools"]} == {"espocrm-create_event", "calendar-list"}


@pytest.mark.asyncio
async def test_on_demand_hides_skill_tools_until_loaded():
    fake = FakeLiteLLM(tools=[mcp_tool("espocrm-create_event"), mcp_tool("calendar-list", server="cal")])
    seen_tools: list[set[str]] = []

    def capture(reply):
        def step(body):
            seen_tools.append({t["function"]["name"] for t in body.get("tools", [])})
            return reply

        return step

    fake.chat_script = [
        capture(tool_reply((LOAD_SKILL, {"name": "crm"}))),
        capture(tool_reply(("espocrm-create_event", {"title": "RAG"}))),
        capture(text_reply("ok")),
    ]
    answer, statuses = await run(fake, agent("skills_loading: on_demand\n"))

    assert answer == "ok"
    assert seen_tools[0] == {"calendar-list", LOAD_SKILL}
    assert seen_tools[1] == {"calendar-list", "espocrm-create_event"}
    assert statuses == ["Loading skill crm", "Calling espocrm-create_event"]
    first_prompt = fake.calls_to("POST", "/v1/chat/completions")[0].body["messages"][0]["content"]
    assert "Use the CRM tools." not in first_prompt and "- crm: CRM work." in first_prompt


@pytest.mark.asyncio
async def test_allowlist_and_unavailable_tool():
    fake = FakeLiteLLM(tools=[mcp_tool("espocrm-create_event"), mcp_tool("calendar-list", server="cal")])
    fake.chat_script = [tool_reply(("calendar-list", {})), text_reply("sorry")]
    answer, _ = await run(fake, agent('tools: ["espocrm-*"]\n', skills=False))
    assert answer == "sorry"
    assert fake.calls_to("POST", "/mcp-rest/tools/call") == []
    tool_msg = fake.calls_to("POST", "/v1/chat/completions")[1].body["messages"][-1]
    assert "not available" in tool_msg["content"]


@pytest.mark.asyncio
async def test_out_of_turns_forces_a_final_answer_and_truncates_results():
    fake = FakeLiteLLM(tools=[mcp_tool("espocrm-create_event")])
    fake.tool_results["espocrm-create_event"] = {"content": [{"type": "text", "text": "x" * 500}]}
    fake.chat_script = [tool_reply(("espocrm-create_event", {})), tool_reply(("espocrm-create_event", {})), text_reply("partial")]
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
