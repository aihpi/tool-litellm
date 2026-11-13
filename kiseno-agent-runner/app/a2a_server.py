"""Serves each agent as an A2A endpoint at /agents/<name>/ (card at /agents/<name>/.well-known/agent-card.json).

Only LiteLLM may call it: JSON-RPC requests need `Authorization: Bearer <RUNNER_INBOUND_TOKEN>` (LiteLLM sends the
agent's litellm_params.api_key) and the `X-LiteLLM-User-Id` LiteLLM forwards for the human behind the call. Without
that id the agent key would run with the agent's full grants, so such requests are refused. The card is served
without auth: it holds only name, description and URL, and LiteLLM fetches it before every call.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import Callable
from typing import Any

from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types.a2a_pb2 import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Part, TaskState
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import get_route_path
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import Settings
from app.litellm_client import Caller, LiteLLMGateway
from app.loop import LoopConfig, run_agent
from app.sync import AgentRuntime, Registry

logger = logging.getLogger(__name__)


def agent_card(runtime: AgentRuntime, settings: Settings) -> AgentCard:
    spec = runtime.definition.spec
    return AgentCard(
        name=spec.name,
        description=spec.description,
        version=runtime.fingerprint[:16],
        supported_interfaces=[AgentInterface(url=settings.agent_url(spec.name), protocol_binding="JSONRPC", protocol_version="1.0")],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[AgentSkill(id=s.name, name=s.name, description=s.description) for s in runtime.definition.skills],
    )


class RunnerAgentExecutor(AgentExecutor):
    def __init__(self, name: str, registry: Registry, gateway: LiteLLMGateway, settings: Settings):
        self.name = name
        self.registry = registry
        self.gateway = gateway
        self.settings = settings

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            if context.message is None:
                raise ValueError("request has no message")
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        headers = {k.lower(): v for k, v in (context.call_context.state.get("headers") or {}).items()}
        caller = Caller(
            user_id=headers.get("x-litellm-user-id", ""),
            team_id=headers.get("x-litellm-team-id") or None,
            trace_id=headers.get("x-litellm-trace-id") or None,
        )
        runtime = self.registry.get(self.name)
        if runtime is None or not caller.user_id:
            await updater.failed(updater.new_agent_message([Part(text="This agent is not ready yet. Try again in a minute.")]))
            return

        spec = runtime.definition.spec
        config = LoopConfig(
            model=spec.model or self.settings.default_model,
            max_turns=min(spec.max_turns, self.settings.max_turns_cap),
            tool_result_max_chars=self.settings.tool_result_max_chars,
            temperature=spec.temperature,
        )

        async def on_status(text: str) -> None:
            await updater.update_status(TaskState.TASK_STATE_WORKING, message=updater.new_agent_message([Part(text=text)]))

        try:
            async with asyncio.timeout(self.settings.request_timeout_seconds):
                answer = await run_agent(
                    runtime.definition,
                    agent_key=runtime.key,
                    caller=caller,
                    user_text=context.get_user_input(),
                    gateway=self.gateway,
                    config=config,
                    on_status=on_status,
                )
        except TimeoutError:
            await updater.failed(updater.new_agent_message([Part(text="The agent took too long and was stopped.")]))
            return
        except Exception as exc:
            logger.exception("agent %s failed (trace %s)", self.name, caller.trace_id)
            await updater.failed(updater.new_agent_message([Part(text=f"The agent failed: {exc}")]))
            return

        await updater.add_artifact([Part(text=answer)], name="answer")
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.task_id and context.context_id:
            await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


def build_agent_app(runtime: AgentRuntime, registry: Registry, gateway: LiteLLMGateway, settings: Settings) -> Starlette:
    card = agent_card(runtime, settings)
    handler = DefaultRequestHandler(
        agent_executor=RunnerAgentExecutor(runtime.definition.name, registry, gateway, settings),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    routes = create_agent_card_routes(card) + create_jsonrpc_routes(handler, rpc_url="/", enable_v0_3_compat=True)
    return Starlette(routes=routes)


class AgentsDispatcher:
    """ASGI app mounted at /agents: checks auth, then hands the request to that agent's A2A app.

    Agent apps are rebuilt when an agent's fingerprint changes, so a merged change takes effect without a restart.
    """

    def __init__(self, registry: Registry, settings: Settings, app_factory: Callable[[AgentRuntime], ASGIApp]):
        self.registry = registry
        self.settings = settings
        self.app_factory = app_factory
        self._apps: dict[str, tuple[str, ASGIApp]] = {}

    def _app_for(self, runtime: AgentRuntime) -> ASGIApp:
        cached = self._apps.get(runtime.definition.name)
        if cached is None or cached[0] != runtime.fingerprint:
            cached = (runtime.fingerprint, self.app_factory(runtime))
            self._apps[runtime.definition.name] = cached
        return cached[1]

    def _authorized(self, headers: dict[str, str]) -> bool:
        auth = headers.get("authorization", "")
        scheme, _, token = auth.partition(" ")
        return scheme.lower() == "bearer" and hmac.compare_digest(token.strip().encode(), self.settings.runner_inbound_token.encode())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        path = get_route_path(scope)  # "/<name>/..." relative to the /agents mount
        name, _, _rest = path.lstrip("/").partition("/")
        runtime = self.registry.get(name)
        if runtime is None:
            await JSONResponse({"error": f"unknown agent {name!r}"}, status_code=404)(scope, receive, send)
            return
        if scope["method"] != "GET":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            if not self._authorized(headers):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
            if not headers.get("x-litellm-user-id", "").strip():
                await JSONResponse({"error": "X-LiteLLM-User-Id is required"}, status_code=400)(scope, receive, send)
                return
        child: dict[str, Any] = dict(scope)
        child["root_path"] = f"{scope.get('root_path', '')}/{name}"
        await self._app_for(runtime)(child, receive, send)
