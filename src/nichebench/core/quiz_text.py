"""Shared helpers for rendering quiz choice text.

Quiz option text is produced in two places: the YAML dataset (loaded by
:mod:`nichebench.core.framework_loader`) and the MUT prompt (built by
:mod:`nichebench.providers.mut_prompt_composer`).  Both need the same two
operations:

* render a raw YAML value as display text (a few dataset files contain an
  unquoted ``key: value`` inside the option text, which YAML parses as a
  mapping), and
* strip the redundant letter prefix (``A) text`` → ``text``).

Keeping both operations in one module avoids two copies of the same regex
drifting apart.  All helpers are pure: they never mutate their input.

The module also owns the source-text helpers the quiz data tooling needs:
reading the raw source of every option out of a YAML file and telling
whether YAML truncated an option at a comment.  The audit script and the
rebalance script use the same helpers, so they cannot disagree about what
the dataset actually says.

Public API
----------
* :func:`strip_choice_prefix` — remove repeated leading letter markers.
* :func:`render_choice_scalar` — render a scalar the way the YAML source did.
* :func:`render_choice` — render any raw choice value as display text.
* :func:`normalize_choice` — render a choice and strip its letter prefix.
* :func:`normalize_choices` — normalise a raw ``choices`` value into a list.
* :func:`has_choice_prefix` — whether a choice still carries a letter prefix.
* :func:`read_choice_sources` — read the raw source of every option.
* :func:`authored_text` — the text an option was authored with.
* :func:`split_at_comment` — split an option source at its inline comment.
* :func:`find_truncated_options` — find the options YAML truncated.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

# A choice is "prefixed" when it starts with a redundant letter marker such as
# ``A)``, ``(A)``, ``A.``, ``A:`` or ``A -``.  The marker may be repeated
# (``A) A) ...``), so it is stripped repeatedly until the text is stable.
CHOICE_PREFIX_RE = re.compile(r"^\s*\(?\s*[A-Fa-f]\s*[\)\].:\-]\s*")


def strip_choice_prefix(text: str) -> str:
    """Remove redundant leading letter prefixes until the text is stable."""
    current = str(text)
    while True:
        stripped = CHOICE_PREFIX_RE.sub("", current, count=1)
        if stripped == current:
            return current.strip()
        current = stripped


def render_choice_scalar(value: Any) -> str:
    """Render a scalar the way it looked in the original YAML source.

    Booleans are rendered lower-case (``true`` / ``false``) so an option such
    as ``module_name.hooks_converted: true`` keeps its original wording.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def render_choice(value: Any) -> str:
    """Render a raw choice value as display text.

    Most choices are plain strings.  A few dataset files contain an unquoted
    ``key: value`` inside the option text, which YAML parses as a mapping
    instead of a string.  Those are rendered back to ``key: value``; never
    call ``str(dict)`` on them.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if len(value) == 1:
            key, item = next(iter(value.items()))
            return f"{key}: {render_choice_scalar(item)}"
        return ", ".join(f"{key}: {render_choice_scalar(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(render_choice(item) for item in value)
    return render_choice_scalar(value)


def normalize_choice(value: Any) -> str:
    """Render a choice and strip its redundant letter prefix."""
    return strip_choice_prefix(render_choice(value))


def normalize_choices(values: Any) -> Optional[List[str]]:
    """Normalise a raw ``choices`` value into a list of display strings."""
    if not isinstance(values, list):
        return None
    return [normalize_choice(choice) for choice in values]


def has_choice_prefix(value: Any) -> bool:
    """Whether the choice text still carries a redundant letter prefix.

    The canonical prefix predicate: it asks exactly the question
    :func:`normalize_choice` answers with a changed string, by comparing the
    rendered text with the text after :func:`strip_choice_prefix`.  Tooling
    that only *counts* prefixes (the quiz data audit, the data contract test)
    must use this instead of keeping its own prefix regex, otherwise the
    counts drift away from ``normalized_choices_sha256``.
    """
    return normalize_choice(value) != render_choice(value).strip()


# ---------------------------------------------------------------------------
# Source text helpers
# ---------------------------------------------------------------------------


def read_choice_sources(text: str) -> Optional[List[str]]:
    """Return the raw source of every choice item, or ``None``.

    The quiz files use a plain block sequence for ``choices``.  The source of
    an item is the raw text of the item without the ``- `` marker.  Items
    that span several lines (YAML folds a long plain scalar over several
    lines) are returned as one item with the lines joined by a newline.
    Returns ``None`` when the block is not a simple block sequence.
    """
    lines = text.splitlines()
    try:
        start = lines.index("choices:")
    except ValueError:
        return None

    items: List[str] = []
    current: Optional[List[str]] = None
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            if current is not None:
                items.append("\n".join(current))
            current = [stripped[2:]]
            continue
        if current is not None and line.startswith(" "):
            # A continuation line of the previous item.
            current.append(stripped)
            continue
        break
    if current is not None:
        items.append("\n".join(current))
    return items


def authored_text(item: str) -> str:
    """Return the text an option was authored with.

    A quoted item is unquoted with the YAML parser; a plain item is folded
    the way YAML folds it (a line break becomes a single space).  For a plain
    item the text behind a ``#`` comment is part of the authored text, even
    though YAML drops it.
    """
    if item[:1] in ("'", '"'):
        try:
            value = yaml.safe_load(item)
        except Exception:  # noqa: BLE001 - fall back to the raw source
            return item.strip()
        if isinstance(value, str):
            return value
    return " ".join(part.strip() for part in item.splitlines()).strip()


def _inline_comment_start(line: str) -> Optional[int]:
    """Index of the ``#`` that starts an inline YAML comment, or ``None``.

    A ``#`` only starts a comment when it follows whitespace; inside a plain
    scalar ``foo#bar`` the ``#`` is ordinary text.
    """
    for index, char in enumerate(line):
        if char != "#":
            continue
        if index == 0 or line[index - 1] in " \t":
            return index
    return None


def split_at_comment(item: str) -> Tuple[str, Optional[str]]:
    """Split an option source at its inline YAML comment.

    Returns ``(text_before_comment, comment)``.  ``comment`` is ``None`` when
    the source has no inline comment.  A comment-only line does not truncate
    anything - it ends the plain scalar - so it is not reported as one.
    """
    if item[:1] in ("'", '"'):
        return item, None

    kept: List[str] = []
    for line in item.splitlines():
        if line.strip().startswith("#"):
            # A comment-only line ends the scalar but truncates no text.
            continue
        start = _inline_comment_start(line)
        if start is not None:
            kept.append(line[:start])
            return "\n".join(kept), line[start:].strip()
        kept.append(line)
    return item, None


def find_truncated_options(raw_choices: Sequence[Any], sources: Sequence[str]) -> List[Dict[str, Any]]:
    """Find the options that YAML truncated at a comment.

    An option is truncated when the parsed value is shorter than the
    authored text because an unquoted ``#`` started a YAML comment.  The
    returned records carry the normalised parsed text (``parsed``), the
    authored text up to the comment (``authored_prefix``), the comment
    itself and the verbatim source line (``source``) that has to be quoted
    to repair the file.
    """
    truncated: List[Dict[str, Any]] = []
    if not sources or len(sources) != len(raw_choices):
        return truncated

    for index, (raw, source) in enumerate(zip(raw_choices, sources)):
        parsed_raw = render_choice(raw)
        if parsed_raw == authored_text(source):
            continue
        before, comment = split_at_comment(source)
        if comment is None:
            continue
        prefix = authored_text(before)
        if parsed_raw != prefix:
            continue
        truncated.append(
            {
                "index": index,
                "parsed": strip_choice_prefix(parsed_raw),
                "parsed_raw": parsed_raw,
                "authored_prefix": strip_choice_prefix(prefix),
                "comment": comment,
                "authored": strip_choice_prefix(authored_text(source)),
                "source": source,
            }
        )
    return truncated
