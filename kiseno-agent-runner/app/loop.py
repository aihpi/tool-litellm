"""The tool loop: one model call after another until the agent answers without a tool call, or runs out of turns.

Skills reach the model in one of two ways (agent.yaml `skills_loading`):
  all        every skill's text sits in the system prompt and every allowed tool is offered up front;
  on_demand  the prompt lists skill names and descriptions plus `load_skill`; loading a skill returns its text and
             adds the tools its front matter names. Tools that no skill names are always offered.
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.litellm_client import Caller, LiteLLMGateway, McpTool
from app.spec import AgentDefinition, Skill

LOAD_SKILL = "load_skill"
_OPENAI_NAME = re.compile(r"[^a-zA-Z0-9_-]")

StatusCallback = Callable[[str], Awaitable[None]]


class AgentRunError(Exception):
    pass


def _tool_matches(tool: McpTool, patterns: Iterable[str]) -> bool:
    """Patterns (agent.yaml `tools`, a skill's `tools`) match the server-qualified name ("espocrm-Find_CEvents") or
    the tool's own name ("Find_CEvents")."""
    patterns = list(patterns)
    return _matches(tool.qualified_name, patterns) or _matches(tool.name, patterns)


def _matches(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def allowed_tools(tools: Sequence[McpTool], allowlist: Sequence[str] | None) -> list[McpTool]:
    return list(tools) if allowlist is None else [t for t in tools if _tool_matches(t, allowlist)]


@dataclass
class ToolTable:
    """OpenAI function names (^[a-zA-Z0-9_-]{1,64}$) mapped back to LiteLLM's MCP tool names."""

    by_function: dict[str, McpTool] = field(default_factory=dict)

    @classmethod
    def build(cls, tools: Iterable[McpTool]) -> ToolTable:
        table = cls()
        for tool in tools:
            base = _OPENAI_NAME.sub("_", tool.qualified_name)[:64] or "tool"
            name, n = base, 1
            while name in table.by_function or name == LOAD_SKILL:
                n += 1
                suffix = f"_{n}"
                name = base[: 64 - len(suffix)] + suffix
            table.by_function[name] = tool
        return table

    def function_specs(self, visible: Iterable[str]) -> list[dict[str, Any]]:
        specs = []
        for fn in visible:
            tool = self.by_function[fn]
            parameters = tool.input_schema if tool.input_schema.get("type") == "object" else {"type": "object", "properties": {}}
            specs.append({"type": "function", "function": {"name": fn, "description": tool.description[:1024], "parameters": parameters}})
        return specs


def _load_skill_spec(skills: Sequence[Skill]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": LOAD_SKILL,
            "description": "Load the full instructions of a skill, and the tools it needs, before doing a task it covers.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "enum": [s.name for s in skills]}},
                "required": ["name"],
            },
        },
    }


def system_prompt(agent: AgentDefinition) -> str:
    parts = [agent.instructions] if agent.instructions else []
    if agent.skills and agent.spec.skills_loading == "all":
        parts.append("# Skills\n\n" + "\n\n".join(f"## {s.name}\n{s.description}\n\n{s.body}" for s in agent.skills))
    elif agent.skills:
        listing = "\n".join(f"- {s.name}: {s.description}" for s in agent.skills)
        parts.append(
            "# Skills\n\nCall `load_skill` with a skill's name before a task it covers; it returns the instructions "
            f"and makes the skill's tools available.\n\n{listing}"
        )
    return "\n\n".join(parts)


def _tool_result_text(result: dict[str, Any], limit: int) -> str:
    chunks = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            chunks.append(str(item.get("text", "")))
        elif isinstance(item, dict):
            chunks.append(json.dumps(item, ensure_ascii=False))
    if not chunks and result.get("structuredContent") is not None:
        chunks.append(json.dumps(result["structuredContent"], ensure_ascii=False))
    text = "\n".join(chunks) or "(no output)"
    if result.get("isError"):
        text = f"Tool error: {text}"
    return text if len(text) <= limit else text[:limit] + f"\n[truncated, {len(text) - limit} more characters]"


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"arguments are not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("arguments must be a JSON object")
    return parsed


@dataclass
class LoopConfig:
    model: str
    max_turns: int
    tool_result_max_chars: int
    temperature: float | None = None


async def run_agent(
    agent: AgentDefinition,
    *,
    agent_key: str,
    caller: Caller,
    user_text: str,
    gateway: LiteLLMGateway,
    config: LoopConfig,
    on_status: StatusCallback,
) -> str:
    tools = allowed_tools(await gateway.list_tools(agent_key, caller), agent.spec.tools)
    table = ToolTable.build(tools)
    skills_by_name = {s.name: s for s in agent.skills}
    on_demand = agent.spec.skills_loading == "on_demand" and bool(agent.skills)

    if on_demand:
        skill_patterns = [p for s in agent.skills for p in s.tools]
        visible = [fn for fn, t in table.by_function.items() if not _tool_matches(t, skill_patterns)]
    else:
        visible = list(table.by_function)
    loaded: set[str] = set()

    messages: list[dict[str, Any]] = []
    prompt = system_prompt(agent)
    if prompt:
        messages.append({"role": "system", "content": prompt})
    messages.append({"role": "user", "content": user_text})

    for _turn in range(config.max_turns):
        specs = table.function_specs(visible)
        if on_demand and len(loaded) < len(agent.skills):
            specs.append(_load_skill_spec([s for s in agent.skills if s.name not in loaded]))
        body: dict[str, Any] = {"model": config.model, "messages": messages}
        if specs:
            body["tools"] = specs
            body["tool_choice"] = "auto"
        if config.temperature is not None:
            body["temperature"] = config.temperature

        response = await gateway.chat(agent_key, caller, body)
        choices = response.get("choices") or []
        if not choices:
            raise AgentRunError("the model returned no choices")
        message = choices[0].get("message") or {}
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return (message.get("content") or "").strip() or "(The model returned an empty answer.)"

        messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": tool_calls})
        for call in tool_calls:
            fn = (call.get("function") or {}).get("name") or ""
            call_id = call.get("id") or fn
            try:
                arguments = _parse_arguments((call.get("function") or {}).get("arguments"))
            except ValueError as exc:
                messages.append({"role": "tool", "tool_call_id": call_id, "content": f"Error: {exc}"})
                continue

            if fn == LOAD_SKILL and on_demand:
                skill = skills_by_name.get(str(arguments.get("name")))
                if skill is None:
                    content = f"Error: unknown skill {arguments.get('name')!r}"
                else:
                    await on_status(f"Loading skill {skill.name}")
                    loaded.add(skill.name)
                    for fn_name, tool in table.by_function.items():
                        if fn_name not in visible and _tool_matches(tool, skill.tools):
                            visible.append(fn_name)
                    content = skill.body
                messages.append({"role": "tool", "tool_call_id": call_id, "content": content})
                continue

            tool = table.by_function.get(fn) if fn in visible else None
            if tool is None:
                messages.append({"role": "tool", "tool_call_id": call_id, "content": f"Error: tool {fn!r} is not available"})
                continue
            await on_status(f"Calling {tool.qualified_name}")
            try:
                result = await gateway.call_tool(agent_key, caller, tool, arguments)
                content = _tool_result_text(result, config.tool_result_max_chars)
            except Exception as exc:  # the model sees the failure and can recover or explain it
                content = f"Error calling {tool.qualified_name}: {exc}"
            messages.append({"role": "tool", "tool_call_id": call_id, "content": content})

    # Out of turns: one last call without tools so the user still gets an answer.
    await on_status("Writing the answer")
    messages.append(
        {
            "role": "user",
            "content": "You have used all the steps available for this request. Answer now with what you have, "
            "and say clearly what is still missing.",
        }
    )
    response = await gateway.chat(agent_key, caller, {"model": config.model, "messages": messages})
    choices = response.get("choices") or []
    final = ((choices[0].get("message") or {}).get("content") if choices else None) or ""
    return final.strip() or "(The agent ran out of steps without an answer.)"
