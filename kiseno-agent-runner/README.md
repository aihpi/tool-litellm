# KISENO agent runner

The agent runner executes the KISENO agents (for example Qwen-Bot) behind LiteLLM. It reads agent definitions that Argo CD syncs from the GitLab agents repo, registers each one as an A2A agent in LiteLLM, and runs the tool loop when LiteLLM calls it. It is for the KISENO LLM platform team; colleagues who only write agents and skills need the agents repo, not this folder.

## How it fits

```
Slack bot ──► LiteLLM /a2a/{agent} ──► runner /agents/{agent}/ ──► LiteLLM /v1/chat/completions and /mcp-rest
                                        (Bearer token + X-LiteLLM-User-Id)   (agent key + the caller's headers)
GitLab agents repo ──Argo CD──► ConfigMaps in kiseno-agents ──► runner sync ──► LiteLLM agents and agent keys
```

Every model and tool call runs with the agent's own key and echoes the caller's `X-LiteLLM-User-Id` and `X-LiteLLM-Team-Id`. LiteLLM then caps the agent at what that person may use, so permissions, budgets and spend stay per person. The runner never talks to MCP servers directly; LiteLLM does.

## What the sync does

Every `SYNC_INTERVAL_SECONDS` the runner loads the catalog and reconciles LiteLLM:

1. It creates or updates one LiteLLM agent per `agent.yaml`, pointing at `RUNNER_PUBLIC_URL/agents/<name>/`. The agent's `litellm_params.api_key` is `RUNNER_INBOUND_TOKEN`, which LiteLLM sends back as the bearer token.
2. It mints one key per agent (bound with `agent_id`, scoped to the agent's model and MCP access groups, valid for `AGENT_KEY_DURATION_DAYS`) and keeps it in memory only. It replaces the key shortly before it expires, and on startup it deletes its older keys for that agent.
3. It deletes managed agents whose files are gone. Managed means the card URL starts with `RUNNER_PUBLIC_URL/agents/`; agents an admin created by hand are never touched.

An agent whose files don't parse, or that names an MCP access group it may not use, keeps its current state in LiteLLM and shows up under `invalid` in `/status`. An empty catalog never deletes anything.

## MCP access

An agent can only use the LiteLLM access group named after itself (`ALLOWED_MCP_ACCESS_GROUPS={name}`). Admins give an agent MCP servers by adding them to that group in the LiteLLM UI; a merged agent file cannot grant access. To share a group between agents, set for example `ALLOWED_MCP_ACCESS_GROUPS={name},shared-*` in the deployment.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `LITELLM_BASE_URL` | required | LiteLLM inside the cluster, e.g. `http://litellm-service.litellm.svc:4000` |
| `LITELLM_SYNC_KEY` | required | Proxy-admin key with `allowed_routes` limited to agent management, `/key/generate`, `/key/delete`, `/key/list` |
| `RUNNER_INBOUND_TOKEN` | required | Shared secret LiteLLM sends as bearer token on every A2A call |
| `RUNNER_PUBLIC_URL` | required | How LiteLLM reaches the runner, e.g. `http://kiseno-agent-runner.litellm.svc:8080` |
| `AGENTS_SOURCE` | `kubernetes` | `kubernetes` (ConfigMaps) or `directory` (local checkout) |
| `AGENTS_NAMESPACE` | `kiseno-agents` | Namespace of the agent ConfigMaps |
| `AGENTS_DIR` | none | Agents repo checkout, for `AGENTS_SOURCE=directory` |
| `ALLOWED_MCP_ACCESS_GROUPS` | `{name}` | Access groups an agent may name; wildcards, `{name}` is the agent |
| `SYNC_INTERVAL_SECONDS` | `30` | Time between syncs |
| `AGENT_KEY_DURATION_DAYS` | `30` | Lifetime of each agent key; must fit under any `upperbound_key_generate_params.duration` |
| `AGENT_KEY_RENEW_DAYS` | `3` | Replace an agent key this many days before it expires |
| `DEFAULT_MODEL` | `qwen3-8-27b` | Model when `agent.yaml` names none |
| `MAX_TURNS_CAP` | `20` | Upper bound for `max_turns` in any agent |
| `REQUEST_TIMEOUT_SECONDS` | `600` | Whole request, all turns |
| `LLM_TIMEOUT_SECONDS` | `300` | One model or tool call |
| `TOOL_RESULT_MAX_CHARS` | `20000` | Longer tool results are cut before they reach the model |

## Kubernetes

The deployment lives in `aihpi/litellm-k8s`. The runner needs a ClusterIP Service on port 8080, one replica (agent keys live in memory), and a ServiceAccount with get and list on configmaps in `kiseno-agents`. Probes are `GET /healthz` (liveness) and `GET /readyz` (ready after the first successful sync). `GET /status` shows agents, the last sync report and errors, never keys. Only LiteLLM pods should reach the runner; a NetworkPolicy should admit only them.

## Run locally

```bash
pip install -r kiseno-agent-runner/requirements-dev.txt
```

```bash
cd kiseno-agent-runner && AGENTS_SOURCE=directory AGENTS_DIR=examples/agents-repo LITELLM_BASE_URL=http://localhost:4000 LITELLM_SYNC_KEY=sk-... RUNNER_INBOUND_TOKEN=dev RUNNER_PUBLIC_URL=http://localhost:8080 uvicorn app.main:build --factory --port 8080
```

## Tests

```bash
cd kiseno-agent-runner && python -m pytest tests -q
```

The tests use an in-memory LiteLLM and the same `a2a-sdk` client LiteLLM uses to call agent backends.

## The agents repo

`examples/agents-repo/` is the starting content of the GitLab agents repo: Qwen-Bot, two skills, templates, the kustomize wiring and the CI. `python -m app.validate <checkout>` is the merge request check; it uses the runner's own parser. `schema/agent.schema.json` is the JSON schema of `agent.yaml`; regenerate it with `python -m app.spec > schema/agent.schema.json` after changing `app/spec.py` (a test fails when it is out of date).
