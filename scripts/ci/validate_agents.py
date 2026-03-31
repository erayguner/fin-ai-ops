#!/usr/bin/env python3
"""CI validator for agent definitions.

Validates all .claude/agents/**/*.md files have required frontmatter
fields and valid configurations. Used in CI to prevent agent config drift.

Usage:
    python scripts/ci/validate_agents.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED_FIELDS = {"name", "description", "tools", "model"}
VALID_MODELS = {"haiku", "sonnet", "opus"}

# Directories containing agents that follow the full ECC-style frontmatter
STRICT_DIRS = {"finops"}


def parse_frontmatter(content: str) -> dict[str, str] | None:
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return None

    fields: dict[str, str] = {}
    for line in match.group(1).strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip().strip('"').strip("'")
    return fields


def validate_agent(path: Path) -> list[str]:
    errors = []
    content = path.read_text()

    frontmatter = parse_frontmatter(content)
    if frontmatter is None:
        errors.append(f"{path}: missing YAML frontmatter (---)")
        return errors

    # Determine enforcement level: strict for finops agents, warn for legacy
    is_strict = any(d in path.parts for d in STRICT_DIRS)
    required = REQUIRED_FIELDS if is_strict else {"name", "description"}

    for field in required:
        if field not in frontmatter:
            errors.append(f"{path}: missing required field '{field}'")

    model = frontmatter.get("model", "")
    if model and model not in VALID_MODELS:
        errors.append(f"{path}: invalid model '{model}' (must be one of {VALID_MODELS})")

    if len(content.strip()) < 100 and is_strict:
        errors.append(f"{path}: agent definition too short (likely incomplete)")

    return errors


def main() -> int:
    agents_dir = Path(".claude/agents")
    if not agents_dir.exists():
        print("No .claude/agents directory found")
        return 0

    agent_files = list(agents_dir.rglob("*.md"))
    if not agent_files:
        print("No agent files found")
        return 0

    all_errors: list[str] = []
    for f in sorted(agent_files):
        all_errors.extend(validate_agent(f))

    print(f"Validated {len(agent_files)} agent(s)")

    if all_errors:
        for e in all_errors:
            print(f"  ERROR: {e}")
        print(f"\nFAILED: {len(all_errors)} error(s)")
        return 1

    print("PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
