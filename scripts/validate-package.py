#!/usr/bin/env python3
"""Validate the standalone skill repository without third-party packages."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "orchestrate-it-out"
REQUIRED = (
    ROOT / "README.md",
    ROOT / "LICENSE",
    ROOT / "SECURITY.md",
    ROOT / "CHANGELOG.md",
    ROOT / "install.sh",
    ROOT / "doctor.sh",
    ROOT / ".github/workflows/ci.yml",
    SKILL / "SKILL.md",
    SKILL / "agents/openai.yaml",
    SKILL / "assets/plan-schema.json",
    SKILL / "assets/sol-orchestrator-prompt.md",
    SKILL / "assets/ntm.toml",
    SKILL / "bin/orchestrate-it-out",
    SKILL / "scripts/install",
    SKILL / "scripts/doctor",
    SKILL / "scripts/protocol.py",
    SKILL / "tests/orchestrate-it-out.test.mjs",
)
EXECUTABLES = (
    ROOT / "install.sh",
    ROOT / "doctor.sh",
    SKILL / "bin/orchestrate-it-out",
    SKILL / "scripts/install",
    SKILL / "scripts/doctor",
    SKILL / "scripts/protocol.py",
)


def main() -> int:
    errors: list[str] = []
    for path in REQUIRED:
        if not path.is_file():
            errors.append(f"missing required file: {path.relative_to(ROOT)}")
    for path in EXECUTABLES:
        if path.exists() and not os.access(path, os.X_OK):
            errors.append(f"file is not executable: {path.relative_to(ROOT)}")

    skill_md = SKILL / "SKILL.md"
    if skill_md.is_file():
        content = skill_md.read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---(?:\n|$)", content, re.DOTALL)
        if not match:
            errors.append("SKILL.md has invalid frontmatter delimiters")
        else:
            fields: dict[str, str] = {}
            for line in match.group(1).splitlines():
                if ":" not in line:
                    errors.append(f"invalid frontmatter line: {line!r}")
                    continue
                key, value = line.split(":", 1)
                fields[key.strip()] = value.strip()
            if set(fields) != {"name", "description"}:
                errors.append("SKILL.md frontmatter must contain only name and description")
            if fields.get("name") != "orchestrate-it-out":
                errors.append("SKILL.md name must be orchestrate-it-out")
            description = fields.get("description", "")
            if not description or len(description) > 1024 or "<" in description or ">" in description:
                errors.append("SKILL.md description is empty or invalid")

    metadata = SKILL / "agents/openai.yaml"
    if metadata.is_file() and "$orchestrate-it-out" not in metadata.read_text(encoding="utf-8"):
        errors.append("openai.yaml default prompt must explicitly mention $orchestrate-it-out")

    for schema in sorted((SKILL / "assets").glob("*.json")):
        try:
            json.loads(schema.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            errors.append(f"invalid JSON in {schema.relative_to(ROOT)}: {error}")

    launcher = SKILL / "bin/orchestrate-it-out"
    if launcher.is_file():
        text = launcher.read_text(encoding="utf-8")
        required_fragments = (
            'ORCHESTRATOR_MODEL="gpt-5.6-sol"',
            'WORKER_MODEL="claude-fable-5"',
            '--cc="5:$WORKER_MODEL"',
            '--no-user',
            '--trusted-task',
            'final-gate',
            'validate-send',
        )
        for fragment in required_fragments:
            if fragment not in text:
                errors.append(f"launcher is missing invariant fragment: {fragment}")
        for forbidden in ("reviewer-schema", "review-round", "plan-critic"):
            if forbidden in text:
                errors.append(f"launcher unexpectedly contains reviewer lifecycle: {forbidden}")

    forbidden_patterns = (
        (re.compile(r"/home/[A-Za-z0-9._-]+/"), "absolute Linux home path"),
        (re.compile(r"\bUse when [A-Z][a-z]+ says\b"), "person-specific trigger wording"),
    )
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern, label in forbidden_patterns:
            if pattern.search(text):
                errors.append(f"{label} in {path.relative_to(ROOT)}")

    if errors:
        print("Package validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Package validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
