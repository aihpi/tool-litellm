"""Checks a checkout of the agents repo before merge: `python -m app.validate <path>` (exit code 1 on errors).

It uses the runner's own parser, so the merge request check and the running service agree on what is valid. It also
checks the kustomize wiring, because a file that isn't listed in a folder's kustomization.yaml never reaches the
cluster.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from app.spec import AGENT_LABEL, SKILL_LABEL, SpecError, load_directory, parse_skill

AGENT_FILES = {"agent.yaml", "instructions.md"}
SKILL_FILES = {"SKILL.md"}


def _folders(root: Path, kind: str) -> list[Path]:
    base = root / kind
    if not base.is_dir():
        return []
    return [p for p in sorted(base.iterdir()) if p.is_dir() and not p.name.startswith(("_", "."))]


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SpecError(f"{path.name}: {exc}") from exc


def _check_folder_kustomization(folder: Path, rel: str, label: str, allowed: set[str]) -> list[str]:
    path = folder / "kustomization.yaml"
    if not path.is_file():
        return [f"{rel}: kustomization.yaml is missing (copy it from the _template folder)"]
    try:
        doc = _load_yaml(path) or {}
    except SpecError as exc:
        return [f"{rel}: {exc}"]
    generators = doc.get("configMapGenerator") or []
    if len(generators) != 1 or not isinstance(generators[0], dict):
        return [f"{rel}/kustomization.yaml: expected exactly one configMapGenerator entry"]
    gen = generators[0]
    errors = []
    labels = ((gen.get("options") or {}).get("labels")) or {}
    if labels.get(label) != folder.name:
        errors.append(f"{rel}/kustomization.yaml: options.labels must contain '{label}: {folder.name}'")
    if not ((doc.get("generatorOptions") or {}).get("disableNameSuffixHash") or (gen.get("options") or {}).get("disableNameSuffixHash")):
        errors.append(f"{rel}/kustomization.yaml: set generatorOptions.disableNameSuffixHash: true")
    listed = {str(f).split("=", 1)[-1] for f in gen.get("files") or []}
    present = {p.name for p in folder.iterdir() if p.is_file() and p.name in allowed}
    for name in sorted(present - listed):
        errors.append(f"{rel}: {name} exists but is not listed under files in kustomization.yaml")
    for name in sorted(listed - present):
        errors.append(f"{rel}/kustomization.yaml lists {name}, which does not exist")
    for name in sorted(listed - allowed):
        errors.append(f"{rel}/kustomization.yaml: only {', '.join(sorted(allowed))} may be listed, not {name}")
    return errors


def validate_repo(root: Path) -> list[str]:
    errors: list[str] = []
    catalog = load_directory(root)
    errors += [f"agents/{name}: {reason}" for name, reason in sorted(catalog.invalid.items())]

    for folder in _folders(root, "skills"):
        skill_md = folder / "SKILL.md"
        if not skill_md.is_file():
            errors.append(f"skills/{folder.name}: SKILL.md is missing")
            continue
        try:
            parse_skill(skill_md.read_text(encoding="utf-8"), expected_name=folder.name)
        except SpecError as exc:
            errors.append(f"skills/{folder.name}: {exc}")

    wanted = []
    for kind, label, allowed in (("agents", AGENT_LABEL, AGENT_FILES), ("skills", SKILL_LABEL, SKILL_FILES)):
        for folder in _folders(root, kind):
            rel = f"{kind}/{folder.name}"
            wanted.append(rel)
            errors += _check_folder_kustomization(folder, rel, label, allowed)

    root_kustomization = root / "kustomization.yaml"
    if not root_kustomization.is_file():
        errors.append("kustomization.yaml is missing at the repository root")
    else:
        try:
            resources = {str(r).rstrip("/") for r in (_load_yaml(root_kustomization) or {}).get("resources") or []}
        except SpecError as exc:
            errors.append(str(exc))
        else:
            errors += [f"kustomization.yaml: add '{rel}' under resources" for rel in wanted if rel not in resources]
    return errors


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".")
    errors = validate_repo(root)
    for error in errors:
        print(f"ERROR {error}")
    if not errors:
        print(f"OK: {len(_folders(root, 'agents'))} agents, {len(_folders(root, 'skills'))} skills")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
