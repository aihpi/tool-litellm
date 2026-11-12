import shutil
from pathlib import Path

from app.validate import main, validate_repo

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "agents-repo"


def copy_example(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(EXAMPLE, root)
    return root


def test_example_repo_is_valid():
    assert validate_repo(EXAMPLE) == []
    assert main(["validate", str(EXAMPLE)]) == 0


def test_unlisted_file_and_missing_resource_are_reported(tmp_path):
    root = copy_example(tmp_path)
    (root / "agents" / "qwen-bot" / "kustomization.yaml").write_text(
        (root / "agents" / "qwen-bot" / "kustomization.yaml").read_text().replace("      - instructions.md\n", "")
    )
    new_skill = root / "skills" / "notes"
    shutil.copytree(root / "skills" / "_template", new_skill)
    (new_skill / "SKILL.md").write_text((new_skill / "SKILL.md").read_text().replace("name: my-skill", "name: notes"))
    (new_skill / "kustomization.yaml").write_text((new_skill / "kustomization.yaml").read_text().replace("my-skill", "notes"))

    errors = validate_repo(root)
    assert "agents/qwen-bot: instructions.md exists but is not listed under files in kustomization.yaml" in errors
    assert "kustomization.yaml: add 'skills/notes' under resources" in errors
    assert main(["validate", str(root)]) == 1


def test_wrong_label_and_bad_skill(tmp_path):
    root = copy_example(tmp_path)
    k = root / "skills" / "meeting-summary" / "kustomization.yaml"
    k.write_text(k.read_text().replace("kisz.ai/skill: meeting-summary", "kisz.ai/skill: other"))
    (root / "skills" / "workshop-entry" / "SKILL.md").write_text("no front matter")

    errors = validate_repo(root)
    assert any("options.labels must contain 'kisz.ai/skill: meeting-summary'" in e for e in errors)
    assert any(e.startswith("skills/workshop-entry: SKILL.md must start with YAML front matter") for e in errors)
    assert any(e.startswith("agents/qwen-bot: skills unavailable") for e in errors)
