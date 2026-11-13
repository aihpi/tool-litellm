"""The agent format (`agent.yaml`, `instructions.md`, `skills/<name>/SKILL.md`) and how a catalog is built from it.

The same files arrive two ways: from a checkout of the agents repo (`AGENTS_SOURCE=directory`, for local runs and
tests) or as ConfigMaps that Argo CD syncs into the cluster (`AGENTS_SOURCE=kubernetes`). Both end up in `Catalog`.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

API_VERSION = "kisz.ai/v1"
NAME_PATTERN = r"^[a-z0-9][a-z0-9-]{0,62}$"
AGENT_LABEL = "kisz.ai/agent"
SKILL_LABEL = "kisz.ai/skill"

_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)(.*)\Z", re.DOTALL)


class SlackSpec(BaseModel):
    """How the Slack bot routes to this agent. The runner only carries it; the bot reads it."""

    model_config = ConfigDict(extra="forbid")

    default: bool = Field(default=False, description="Answer DMs and @mentions that no channel rule claims.")
    channels: list[str] = Field(default_factory=list, description="Slack channel IDs this agent answers in.")
    listen: Literal["mentions", "all"] = Field(
        default="mentions", description="In `channels`: answer only @mentions, or read every post."
    )
    dm: bool = Field(default=True, description="Whether people may DM this agent.")


class AgentSpec(BaseModel):
    """`agent.yaml`."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    api_version: Literal["kisz.ai/v1"] = Field(alias="apiVersion")
    name: str = Field(pattern=NAME_PATTERN, description="Lowercase letters, digits and dashes; becomes the LiteLLM agent name.")
    description: str = Field(min_length=1, max_length=500)
    model: str | None = Field(default=None, description="LiteLLM model name; defaults to the runner's DEFAULT_MODEL.")
    max_turns: int = Field(default=8, ge=1, le=50, description="Model calls per request before the agent must answer.")
    temperature: float | None = Field(default=None, ge=0, le=2)
    teams: list[str] = Field(default_factory=list, description="LiteLLM team IDs whose members may use this agent.")
    mcp_access_groups: list[str] | None = Field(
        default=None,
        description="LiteLLM MCP access groups the agent may use. Defaults to [<name>]; admins add servers to that group.",
    )
    skills: list[str] = Field(default_factory=list, description="Names of skills under skills/ in the agents repo.")
    skills_loading: Literal["all", "on_demand"] = Field(
        default="all",
        description="all: every skill and tool in the prompt up front. on_demand: list skills, load one with load_skill().",
    )
    tools: list[str] | None = Field(
        default=None,
        description="Optional allowlist of MCP tool names as LiteLLM lists them; shell-style wildcards allowed.",
    )
    slack: SlackSpec | None = None

    @field_validator("skills", "teams")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("entries must be unique")
        return value

    @property
    def access_groups(self) -> list[str]:
        return self.mcp_access_groups if self.mcp_access_groups is not None else [self.name]


class SkillFrontMatter(BaseModel):
    """Front matter of a SKILL.md (Agent Skills format plus our `tools`). Unknown keys are kept, not rejected."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(pattern=NAME_PATTERN)
    description: str = Field(min_length=1, max_length=1024)
    tools: list[str] = Field(default_factory=list, description="MCP tools (wildcards allowed) this skill needs.")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    tools: tuple[str, ...]
    body: str


@dataclass(frozen=True)
class AgentDefinition:
    spec: AgentSpec
    instructions: str
    skills: tuple[Skill, ...]

    @property
    def name(self) -> str:
        return self.spec.name

    def fingerprint(self, default_model: str) -> str:
        """Changes whenever anything LiteLLM or the loop sees changes."""
        payload = {
            "spec": self.spec.model_dump(mode="json", by_alias=True),
            "model": self.spec.model or default_model,
            "instructions": self.instructions,
            "skills": [[s.name, s.description, list(s.tools), s.body] for s in self.skills],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass
class Catalog:
    agents: dict[str, AgentDefinition] = field(default_factory=dict)
    skills: dict[str, Skill] = field(default_factory=dict)
    # Agents whose files exist but don't parse or reference a missing skill. The sync leaves them as they are in
    # LiteLLM rather than deleting them, so a broken merge never takes a working agent down.
    invalid: dict[str, str] = field(default_factory=dict)


class SpecError(ValueError):
    pass


def parse_skill(text: str, *, expected_name: str | None = None) -> Skill:
    match = _FRONT_MATTER.match(text.lstrip("﻿"))
    if match is None:
        raise SpecError("SKILL.md must start with YAML front matter between '---' lines")
    try:
        raw = yaml.safe_load(match.group(1)) or {}
        front = SkillFrontMatter.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as exc:
        raise SpecError(f"invalid SKILL.md front matter: {exc}") from exc
    if expected_name is not None and front.name != expected_name:
        raise SpecError(f"SKILL.md name {front.name!r} does not match its folder {expected_name!r}")
    return Skill(name=front.name, description=front.description.strip(), tools=tuple(front.tools), body=match.group(2).strip())


def parse_agent_spec(text: str) -> AgentSpec:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SpecError(f"agent.yaml is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise SpecError("agent.yaml must be a mapping")
    try:
        return AgentSpec.model_validate(raw)
    except ValidationError as exc:
        raise SpecError(f"agent.yaml: {exc}") from exc


def build_catalog(
    agent_files: Iterable[tuple[str, Mapping[str, str]]],
    skill_files: Iterable[tuple[str, str]],
) -> Catalog:
    """`agent_files`: (source name, {"agent.yaml": ..., "instructions.md": ...}). `skill_files`: (folder name, SKILL.md text)."""
    catalog = Catalog()
    skill_errors: dict[str, str] = {}
    for folder, text in skill_files:
        try:
            skill = parse_skill(text, expected_name=folder)
        except SpecError as exc:
            skill_errors[folder] = str(exc)
            continue
        catalog.skills[skill.name] = skill

    for source, files in agent_files:
        if "agent.yaml" not in files:
            catalog.invalid[source] = "agent.yaml is missing"
            continue
        try:
            spec = parse_agent_spec(files["agent.yaml"])
        except SpecError as exc:
            catalog.invalid[source] = str(exc)
            continue
        if spec.name != source:
            catalog.invalid[source] = f"agent.yaml name {spec.name!r} does not match its folder or ConfigMap {source!r}"
            continue
        missing = [s for s in spec.skills if s not in catalog.skills]
        if missing:
            details = "; ".join(f"{s}: {skill_errors[s]}" if s in skill_errors else f"{s}: not found" for s in missing)
            catalog.invalid[spec.name] = f"skills unavailable ({details})"
            continue
        catalog.agents[spec.name] = AgentDefinition(
            spec=spec,
            instructions=files.get("instructions.md", "").strip(),
            skills=tuple(catalog.skills[s] for s in spec.skills),
        )
    return catalog


def load_directory(root: Path) -> Catalog:
    """The agents repo layout: agents/<name>/{agent.yaml,instructions.md} and skills/<name>/SKILL.md."""
    agent_files = []
    agents_dir = root / "agents"
    for folder in sorted(agents_dir.iterdir()) if agents_dir.is_dir() else []:
        if not folder.is_dir() or folder.name.startswith(("_", ".")):
            continue
        files = {f.name: f.read_text(encoding="utf-8") for f in folder.iterdir() if f.name in ("agent.yaml", "instructions.md")}
        agent_files.append((folder.name, files))
    skill_files = []
    skills_dir = root / "skills"
    for folder in sorted(skills_dir.iterdir()) if skills_dir.is_dir() else []:
        skill_md = folder / "SKILL.md"
        if folder.is_dir() and skill_md.is_file():
            skill_files.append((folder.name, skill_md.read_text(encoding="utf-8")))
    return build_catalog(agent_files, skill_files)


def load_configmaps(items: Iterable[Mapping[str, Any]]) -> Catalog:
    """ConfigMaps labelled kisz.ai/agent=<name> (keys agent.yaml, instructions.md) or kisz.ai/skill=<name> (key SKILL.md)."""
    agent_files = []
    skill_files = []
    for item in items:
        labels = (item.get("metadata") or {}).get("labels") or {}
        data = item.get("data") or {}
        if AGENT_LABEL in labels:
            agent_files.append((labels[AGENT_LABEL], data))
        elif SKILL_LABEL in labels and "SKILL.md" in data:
            skill_files.append((labels[SKILL_LABEL], data["SKILL.md"]))
    return build_catalog(agent_files, skill_files)


def agent_json_schema() -> dict[str, Any]:
    schema = AgentSpec.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://kisz.ai/schemas/agent-v1.json"
    return schema


if __name__ == "__main__":
    print(json.dumps(agent_json_schema(), indent=2, sort_keys=True))
