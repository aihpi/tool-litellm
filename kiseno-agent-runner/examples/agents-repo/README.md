# Example agents repo

This folder shows the format of an agents repo and is the fixture for the agent runner's tests. KISENO's real agents and skills live in the private repository `agent-skillbook` on gitlab.hpi.de; change them there, not here. A merge into an agents repo goes live once Argo CD has synced it: Argo CD copies the files into the cluster, and the agent runner loads them into LiteLLM.

## Add a skill

A skill is a set of instructions for one kind of task, such as summarizing a meeting.

1. Copy `skills/_template/` to `skills/<skill-name>/`, using lowercase letters, digits and dashes.
2. In `SKILL.md`, set `name` to the folder name and write a `description` that says when to use the skill. The agent decides from this line.
3. Under `tools`, list the MCP tools the skill needs, as LiteLLM lists them. Wildcards work, e.g. `"calendar*"`.
4. In the folder's `kustomization.yaml`, replace `my-skill` with the folder name (twice).
5. Add `skills/<skill-name>` under `resources` in the root `kustomization.yaml`.
6. Add the skill name under `skills` in the agent that should use it, e.g. `agents/qwen-bot/agent.yaml`.
7. Open a merge request. The pipeline checks the files.

The GitLab web editor is enough for all of this; no local Git is needed.

## Add an agent

Copy `agents/_template/` to `agents/<agent-name>/` and follow the comments in its `agent.yaml`. Most new abilities are skills for Qwen-Bot, not new agents. A separate agent makes sense when a group needs different permissions or tools.

## MCP servers

Tools come from MCP servers that admins create in LiteLLM. An agent can only use the LiteLLM access group named after itself; admins add a server to that group (for Qwen-Bot the group `qwen-bot`). A merge request here cannot grant an agent new MCP servers. Ask an admin when a skill needs a server that isn't there yet.

## What the pipeline checks

The `agent-files` job runs the agent runner's own validator, so a file that passes here also loads in the cluster. It checks the `agent.yaml` fields, the `SKILL.md` front matter, that every skill an agent names exists, and that every file is listed in its folder's `kustomization.yaml`. The `kustomize` job builds the ConfigMaps exactly as Argo CD will.
