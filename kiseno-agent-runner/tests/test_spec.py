import json
from pathlib import Path

import pytest

from app.spec import (
    AGENT_LABEL,
    SKILL_LABEL,
    SpecError,
    agent_json_schema,
    build_catalog,
    load_configmaps,
    load_directory,
    parse_agent_spec,
    parse_skill,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "agents-repo"
SCHEMA = Path(__file__).resolve().parents[1] / "schema" / "agent.schema.json"

MINIMAL = "apiVersion: kiseno/v1\nname: helper\ndescription: Helps.\n"
SKILL = "---\nname: notes\ndescription: Take notes.\ntools: [\"espocrm*\"]\nlicense: MIT\n---\n\nDo the thing.\n"


def test_example_repo_loads():
    catalog = load_directory(EXAMPLE)
    assert catalog.invalid == {}
    agent = catalog.agents["qwen-bot"]
    assert [s.name for s in agent.skills] == ["meeting-summary", "workshop-entry"]
    assert agent.spec.access_groups == ["qwen-bot"]
    assert agent.spec.slack is not None and agent.spec.slack.default
    assert agent.instructions.startswith("You are Qwen-Bot")


def test_defaults():
    spec = parse_agent_spec(MINIMAL)
    assert spec.access_groups == ["helper"]
    assert spec.skills_loading == "all"
    assert spec.max_turns == 8
    assert spec.tools is None


@pytest.mark.parametrize(
    "text",
    [
        "apiVersion: kiseno/v2\nname: helper\ndescription: x\n",  # wrong version
        "apiVersion: kiseno/v1\nname: Helper\ndescription: x\n",  # uppercase name
        "apiVersion: kiseno/v1\nname: helper\ndescription: x\nunknown: 1\n",  # typo'd field
        "apiVersion: kiseno/v1\nname: helper\ndescription: x\nskills: [a, a]\n",  # duplicate skill
        "- not a mapping\n",
        "name: [unclosed\n",
    ],
)
def test_invalid_agent_yaml(text):
    with pytest.raises(SpecError):
        parse_agent_spec(text)


def test_skill_front_matter_keeps_standard_fields():
    skill = parse_skill(SKILL, expected_name="notes")
    assert skill.tools == ("espocrm*",)
    assert skill.body == "Do the thing."


def test_skill_without_front_matter_or_wrong_folder():
    with pytest.raises(SpecError):
        parse_skill("# no front matter")
    with pytest.raises(SpecError):
        parse_skill(SKILL, expected_name="other")


def test_missing_skill_marks_agent_invalid_not_dropped_silently():
    catalog = build_catalog([("helper", {"agent.yaml": MINIMAL + "skills: [nope]\n"})], [])
    assert "helper" in catalog.invalid and "nope" in catalog.invalid["helper"]
    assert catalog.agents == {}


def test_name_must_match_folder():
    catalog = build_catalog([("other", {"agent.yaml": MINIMAL})], [])
    assert "other" in catalog.invalid


def test_configmaps():
    items = [
        {"metadata": {"labels": {AGENT_LABEL: "helper"}}, "data": {"agent.yaml": MINIMAL + "skills: [notes]\n", "instructions.md": "Be brief."}},
        {"metadata": {"labels": {SKILL_LABEL: "notes"}}, "data": {"SKILL.md": SKILL}},
    ]
    catalog = load_configmaps(items)
    assert catalog.agents["helper"].instructions == "Be brief."
    assert catalog.agents["helper"].skills[0].name == "notes"


def test_fingerprint_changes_with_content():
    a = build_catalog([("helper", {"agent.yaml": MINIMAL, "instructions.md": "A"})], []).agents["helper"]
    b = build_catalog([("helper", {"agent.yaml": MINIMAL, "instructions.md": "B"})], []).agents["helper"]
    assert a.fingerprint("m") != b.fingerprint("m")
    assert a.fingerprint("m") == a.fingerprint("m")


def test_committed_schema_is_current():
    """schema/agent.schema.json is what the agents repo's CI validates against; regenerate with `python -m app.spec`."""
    assert json.loads(SCHEMA.read_text()) == agent_json_schema()
