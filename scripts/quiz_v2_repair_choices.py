"""Repair quiz YAML choice scalars before the v2 rebalance.

Two defects are repaired without changing any wording:

1. **Truncated strings** — an unquoted choice contains `` #`` so YAML treats
   the rest of the line as a comment. The authored line is the full option;
   quoting it preserves the text.
2. **Mapping-typed choices** — an unquoted ``key: value`` inside the option
   makes YAML parse it as a mapping. The mapping is joined back to
   ``key: value`` and quoted.

The script edits only the choice lines it needs to; it does not reformat the
file. Run ``--dry-run`` first, then ``--write``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from nichebench.core.quiz_text import find_truncated_options, read_choice_sources


def _choice_lines(lines: list[str]) -> list[int]:
    """Return indices of the ``- `` lines inside the top-level choices block."""
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "choices:":
            start = i
            break
    if start is None:
        raise ValueError("no top-level choices: block")

    base_indent = len(lines[start]) - len(lines[start].lstrip())
    out: list[int] = []
    for i in range(start + 1, len(lines)):
        raw = lines[i]
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent <= base_indent:
            break
        if raw.lstrip().startswith("- "):
            out.append(i)
    return out


def repair_file(path: Path, *, write: bool = False) -> tuple[int, int]:
    """Return ``(truncated_repaired, mapping_repaired)`` for one file.

    When ``write`` is false the file is left untouched and the repaired text
    is verified in memory instead.
    """
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("root is not a mapping")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("no choices list")

    lines = text.splitlines()
    indices = _choice_lines(lines)
    if len(indices) != len(choices):
        raise ValueError(f"choice line count {len(indices)} != parsed choices {len(choices)}")

    truncated = 0
    mappings = 0
    for idx, parsed in zip(indices, choices):
        raw = lines[idx]
        prefix = raw[: len(raw) - len(raw.lstrip())] + "- "
        content = raw.lstrip()[2:].strip()

        if isinstance(parsed, dict):
            authored = ": ".join(f"{key}: {value}" for key, value in parsed.items())
            lines[idx] = prefix + json.dumps(authored, ensure_ascii=False)
            mappings += 1
            continue

        if not isinstance(parsed, str):
            raise ValueError(f"choice line {idx + 1}: unsupported type {type(parsed).__name__}")

        # The authored text is the whole scalar after ``- ``. YAML truncated it
        # at the first `` #``; the parsed value is a strict prefix of the line.
        if " #" in content and len(content) > len(parsed) and content.startswith(parsed):
            lines[idx] = prefix + json.dumps(content, ensure_ascii=False)
            truncated += 1

    repaired_text = "\n".join(lines) + "\n"
    if write and (truncated or mappings):
        path.write_text(repaired_text, encoding="utf-8")

    # Verify the repaired text parses cleanly into string choices.
    repaired = yaml.safe_load(repaired_text)
    repaired_choices = repaired.get("choices") if isinstance(repaired, dict) else None
    if not isinstance(repaired_choices, list):
        raise ValueError("post-repair choices is not a list")
    bad = [c for c in repaired_choices if not isinstance(c, str)]
    if bad:
        raise ValueError(f"post-repair non-string choices: {[type(c).__name__ for c in bad]}")
    sources = read_choice_sources(repaired_text)
    if sources is None:
        raise ValueError("post-repair choices block could not be read")
    if find_truncated_options(repaired_choices, sources):
        raise ValueError("post-repair truncation still detected")

    return truncated, mappings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)

    if args.dry_run == args.write:
        parser.error("choose exactly one of --dry-run or --write")
    if not args.data_dir.is_dir():
        parser.error(f"data dir does not exist: {args.data_dir}")

    total_truncated = 0
    total_mappings = 0
    changed: list[tuple[str, int, int]] = []
    for path in sorted(args.data_dir.glob("*.yaml")):
        truncated, mappings = repair_file(path, write=args.write)
        total_truncated += truncated
        total_mappings += mappings
        if truncated or mappings:
            changed.append((path.name, truncated, mappings))

    for name, truncated, mappings in changed:
        print(f"  {name}: truncated={truncated} mapping={mappings}")
    print(f"files changed: {len(changed)}")
    print(f"truncated choices repaired: {total_truncated}")
    print(f"mapping choices repaired: {total_mappings}")

    if args.dry_run:
        print("dry-run: no files written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
