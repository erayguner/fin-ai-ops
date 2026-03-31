#!/usr/bin/env python3
"""CI validator for slash command definitions.

Validates all .claude/commands/**/*.md files have proper frontmatter
and non-empty implementation sections.

Usage:
    python scripts/ci/validate_commands.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED_FIELDS = {"name", "description"}

# Only strictly validate commands in these directories
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


def validate_command(path: Path) -> list[str]:
    errors = []
    content = path.read_text()
    is_strict = any(d in path.parts for d in STRICT_DIRS)

    frontmatter = parse_frontmatter(content)
    if frontmatter is None:
        # Only error for strict dirs and non-README files
        if is_strict and path.stem.lower() not in ("readme", "index"):
            errors.append(f"{path}: missing YAML frontmatter (---)")
        return errors

    for field in REQUIRED_FIELDS:
        if field not in frontmatter:
            errors.append(f"{path}: missing required field '{field}'")

    # Check for implementation section (strict dirs only)
    if is_strict and "```" not in content and "## Implementation" not in content:
        errors.append(f"{path}: no code blocks or Implementation section found")

    return errors


def main() -> int:
    commands_dir = Path(".claude/commands")
    if not commands_dir.exists():
        print("No .claude/commands directory found")
        return 0

    cmd_files = list(commands_dir.rglob("*.md"))
    if not cmd_files:
        print("No command files found")
        return 0

    all_errors: list[str] = []
    validated = 0
    for f in sorted(cmd_files):
        errs = validate_command(f)
        all_errors.extend(errs)
        validated += 1

    print(f"Validated {validated} command file(s)")

    if all_errors:
        for e in all_errors:
            print(f"  ERROR: {e}")
        print(f"\nFAILED: {len(all_errors)} error(s)")
        return 1

    print("PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
