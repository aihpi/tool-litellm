# KISENO Slack bot

Qwen-Bot lets KISENO team members talk to an agent on `qwen3-8-27b` in Slack. It is a small Python service built on `slack_bolt` in Socket Mode: it opens an outbound WebSocket to Slack and needs no public request URL, no Service and no Ingress. Every Slack message runs as the writer's own LiteLLM user, with that user's permissions, budget and spend, never as a shared bot key. This README is for the people who deploy and operate the bot.

## How a message flows

```text
Slack ──(Socket Mode, outbound)── kiseno-slack-bot ──► litellm-service:4000 /a2a/{agent_id}/message/stream   (per-user key)
                                                          │
                                                          ▼
                                                   kiseno-agent-runner (tool loop, MCP, skills)
```

1. Slack delivers an `app_mention`, a DM (`message.im`) or a channel message (`message.channels`, `message.groups`) over the socket.
2. The bot picks the agent (see "Routing") and checks the writer (see "Identity").
3. It mints or reuses a per-user LiteLLM key and calls `POST {LITELLM_BASE_URL}/a2a/{agent_id}/message/stream`.
4. It posts one placeholder in the thread, adds an `eyes` reaction to the request, and edits the placeholder at most every 1.5 seconds with status text ("calling tool X") and then the answer.

## Environment variables

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `SLACK_APP_TOKEN` | yes | | App-level token (`xapp-…`) with scope `connections:write`. Secret. |
| `SLACK_BOT_TOKEN` | yes | | Bot token (`xoxb-…`). Secret. |
| `SLACK_TEAM_ID` | no | from `auth.test` | Override for the bot's workspace ID (`T…`). By default the bot reads its workspace and, on Enterprise Grid, its organization (`E…`) from `auth.test` with the bot token at startup. |
| `LITELLM_BASE_URL` | yes | | LiteLLM inside the cluster, for example `http://litellm-service.litellm-staging.svc:4000`. |
| `LITELLM_MINT_KEY` | yes | | Management-type proxy-admin key with no team, restricted to the routes listed below. Secret. |
| `LITELLM_TEAM_ID` | no | | LiteLLM team of the bot, for example `qwen-bot-users`. Only its members get answers, and every per-user key is minted in this team. |
| `AGENTS_NAMESPACE` | no | `kiseno-agents` | Namespace with the agent ConfigMaps. |
| `AGENTS_POLL_SECONDS` | no | `60` | How often the bot re-reads the agent ConfigMaps. |
| `LLM_PORTAL_URL` | no | | Shown in the "log in once at the LLM portal" reply and, unless `ACCESS_HELP_TEXT` is set, in the reply to people without access. |
| `ACCESS_HELP_TEXT` | no | | The one-line reply to LiteLLM users outside the bot's team or the agent's teams, for example whom to ask for access. |
| `LOG_LEVEL` | no | `INFO` | Python log level. |

The bot never logs tokens or keys. A log filter also replaces anything that looks like `sk-…`, `xoxb-…` or `xapp-…` with `[REDACTED]`, and the `httpx` logger stays at WARNING because `/user/list` URLs carry email addresses.

## The minting key

The minting key is a Management-type proxy-admin key with no team. Its `allowed_routes` are limited to these routes:

```json
["/key/generate", "/user/list", "/team/info", "/v1/agents"]
```

LiteLLM resolves `/a2a/{agent}` by name or by id, but the per-user key's `object_permission.agents` only accepts agent ids, so the bot reads `GET /v1/agents` (cached for five minutes) to map agent names to ids. `allowed_routes` cannot restrict HTTP methods, so this key could also create or change agents; keep it inside the cluster and treat it like the master key.

## Kubernetes RBAC

The bot reads ConfigMaps labelled `kiseno/agent` in `AGENTS_NAMESPACE` with its in-cluster service account. It polls every 60 seconds, so `get` and `list` are enough today; `watch` is included so a later switch to a watch needs no RBAC change.

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: kiseno-slack-bot-agents-reader
  namespace: kiseno-agents
rules:
  - apiGroups: [""]
    resources: ["configmaps"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: kiseno-slack-bot-agents-reader
  namespace: kiseno-agents
subjects:
  - kind: ServiceAccount
    name: kiseno-slack-bot
    namespace: litellm-staging
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: kiseno-slack-bot-agents-reader
```

Change the subject's namespace to the one the bot runs in. The bot needs outbound access to `slack.com` and `wss-*.slack.com`, and to the LiteLLM Service. It accepts no inbound connections. Run one replica per Slack app: two replicas with the same app token would split the events between them.

## Slack app

Create the app at api.slack.com/apps from this manifest. For staging, change the name to "Qwen-Bot (staging)" and create a separate app, so staging and prod never share a token.

```yaml
display_information: {name: Qwen-Bot, description: KISENO workflows on Qwen via the LLM gateway}
features:
  bot_user: {display_name: Qwen-Bot, always_online: true}
  app_home: {messages_tab_enabled: true, messages_tab_read_only_enabled: false}
oauth_config:
  scopes:
    bot: [app_mentions:read, chat:write, im:history, im:read, im:write, channels:history, groups:history, users:read, users:read.email, reactions:write]
settings:
  event_subscriptions: {bot_events: [app_mention, message.im, message.channels, message.groups]}
  interactivity: {is_enabled: false}
  org_deploy_enabled: false
  socket_mode_enabled: true
  token_rotation_enabled: false
```

Generate the app-level token (scope `connections:write`) under Basic Information, install the app, and copy the bot token from OAuth & Permissions. Invite the bot into `#workflows` with `/invite @Qwen-Bot`.

## Agent definitions

Each agent is a ConfigMap with the label `kiseno/agent` and a key `agent.yaml` in format `apiVersion: kiseno/v1`. The bot reads only `name`, `teams` and `slack` and ignores every other field, so the runner's fields (`model`, `skills`, `tools` and so on) can change freely.

```yaml
apiVersion: kiseno/v1
name: qwen-bot
teams: [qwen-bot-users]  # LiteLLM team ids
slack:
  default: true          # gets DMs and mentions in channels that no agent lists
  channels: [C0123456]   # channel IDs, not names
  listen: mentions       # or "all" to answer every post in these channels
  dm: true               # answer DMs (only for the default agent)
```

`slack.default`, `slack.dm` and `slack.listen` default to `false`, `false` and `mentions`. ConfigMaps with invalid YAML, another `apiVersion` or a repeated name are skipped with a warning. If the Kubernetes API fails, the bot keeps the last agents it read.

## Routing

A mention goes to the agent that lists the channel in `slack.channels`, otherwise to the agent with `slack.default: true`. A DM goes to the default agent if it has `slack.dm: true`. Any other channel message is answered only if the channel's agent has `slack.listen: all`; a channel message that mentions the bot is left to its `app_mention` event, so each post is answered once.

The bot ignores messages from bots (including itself), edits, deletions, joins and other subtypes. It accepts plain messages, `file_share` and `thread_broadcast`. Answers go into the thread of the request; a top-level DM gets a top-level answer.

## Identity

1. `users.info` refuses bots, guests (`is_restricted`, `is_ultra_restricted`), Slack Connect users (`is_stranger`) and deactivated users. On Enterprise Grid it accepts users whose `enterprise_user.enterprise_id` is the bot's organization, so colleagues from sibling workspaces of the same organization get answers; without Grid the user's `team_id` must be the bot's workspace. Events whose envelope names another organization or workspace are dropped, but the envelope is never taken as proof of who wrote a message, since Slack Connect users arrive in the same envelope.
2. The profile email goes to `GET /user/list?user_email=…`. LiteLLM matches partially, so the bot keeps only a case-insensitive exact match. With no match the writer is asked to log in once at the LLM portal; with several matches the bot refuses and asks for an admin.
3. The LiteLLM user must be in `members_with_roles` of the bot's team (`LITELLM_TEAM_ID`) and of one of the agent's `teams` (`GET /team/info`, cached for 60 seconds). Everyone else gets the one-liner from `ACCESS_HELP_TEXT`, or a default that points to `LLM_PORTAL_URL`.
4. The bot mints a key with `POST /key/generate`: `user_id`, `duration: "24h"`, `allowed_routes: ["agent_inference_routes"]`, `object_permission.agents` set to the ids of every agent the user may use, `team_id` set to `LITELLM_TEAM_ID` when it is set, and `metadata: {managed_by: kiseno-slack-bot, slack_user_id}`. With the team on the key, the team's budget, model access and MCP grants apply, and spend shows under the team. LiteLLM intersects the key's agent grants with the team's, so the team must either leave agents unrestricted or include the bot's agents. The key stays in memory and is reused until one hour before it expires, or until the set of agents changes. If LiteLLM rejects a cached key with 401, the bot mints a new one and retries once.

Refusals in channels are ephemeral, so only the writer sees them. Keys from before a restart are not deleted; they expire after 24 hours.

## What the agent receives

The request is A2A v0.3 JSON-RPC (`message/stream`) with one user Message and one TextPart. The text is the conversation as `<display name>: <text>` lines with the current request as the last line. In a thread that is the thread from `conversations.replies` (last 50 messages); in a top-level DM it is the last 20 DM messages. Mentions become `@name` and Slack links become `label (url)`. The bot's own earlier answers appear under its display name.

`params.metadata.slack` carries `{team, channel, thread_ts, user}` with Slack IDs. The bot reads `message`, `task`, `status-update` and `artifact-update` events from the SSE stream. Status text from working states is shown in italics while the agent works; artifact text, an agent Message or the text of the final status becomes the answer. A failed task, a JSON-RPC error or an HTTP error ends in a one-line message without stack traces; budget and rate-limit messages from LiteLLM are passed on in one line.

## Local development

```bash
cd kiseno-slack-bot
```

```bash
python3.11 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements-dev.txt
```

```bash
.venv/bin/pytest
```

From the repository root, `pytest kiseno-slack-bot/tests` runs the same suite; `kiseno-slack-bot/pytest.ini` keeps it independent of the LiteLLM test configuration. Slack is replaced by a fake client and LiteLLM and the Kubernetes API by `httpx.MockTransport`, so the tests need no network.

The code lives in `app/`: `agents.py` (ConfigMaps and the agent store), `identity.py` (the checks and the key cache), `litellm_client.py`, `a2a.py` (request and stream events), `bot.py` (routing and the reply), `responder.py` (the throttled message edit), `slack_format.py` and `main.py`.

## Container image

Build from the repository root:

```bash
docker build -f kiseno-slack-bot/Dockerfile -t ghcr.io/aihpi/tool-kiseno-slack-bot:aihpi-provider .
```

`.github/workflows/build-kiseno-slack-bot.yaml` runs the tests on every pull request that touches `kiseno-slack-bot/`, and publishes the image on pushes to `aihpi-provider` and `dev/*`. The container runs as UID 10001 and starts with `python -m app.main`. The Deployment lives in `litellm-k8s`.
