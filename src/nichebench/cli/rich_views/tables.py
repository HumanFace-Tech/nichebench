"""Reusable rich table components for nichebench CLI."""

from typing import Iterable, List, Mapping, Optional

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from nichebench.core.quiz_text import normalize_choice

# Canonical display order of task categories.  Categories that are not
# listed here are appended alphabetically after the known ones.
CANONICAL_CATEGORY_ORDER = ("quiz", "code_generation", "bug_fixing", "runtime", "code_agent")

# Categories removed from the harness (non-runnable — dispatch rejects them)
# whose task data still exists.  Hidden unless the user explicitly asks for
# them with ``--all`` (historical discovery).
DEPRECATED_CATEGORIES = frozenset({"code_agent"})


def sort_task_specs(tasklist: Iterable, *, include_deprecated: bool = False) -> List:
    """Return task specs in the canonical category order.

    Removed (non-runnable) categories are dropped unless
    ``include_deprecated`` is set.
    """
    specs = list(tasklist)
    order = {name: index for index, name in enumerate(CANONICAL_CATEGORY_ORDER)}
    specs.sort(key=lambda ts: (order.get(ts.task_type, len(order)), ts.task_type))
    if not include_deprecated:
        specs = [ts for ts in specs if ts.task_type not in DEPRECATED_CATEGORIES]
    return specs


def _category_label(task_type: str) -> str:
    """Render a category cell, marking removed (non-runnable) categories."""
    if task_type in DEPRECATED_CATEGORIES:
        return f"[dim]{task_type} (removed, non-runnable)[/dim]"
    return f"[green]{task_type}[/green]"


def _print_deprecated_note(console: Console, tasklist: Iterable, *, include_deprecated: bool) -> None:
    """Print a note about removed, non-runnable categories.

    When the categories are hidden the note does not name them, so the
    default listing stays free of the removed category names.
    """
    removed = sorted({ts.task_type for ts in tasklist if ts.task_type in DEPRECATED_CATEGORIES})
    if not removed:
        return
    names = ", ".join(removed)
    if include_deprecated:
        if len(removed) == 1:
            subject = f"{names} has been removed and is not runnable"
        else:
            subject = f"{names} have been removed and are not runnable"
        console.print(f"[dim]Note:[/dim] {subject}; historical discovery only.")
    else:
        label = "category is" if len(removed) == 1 else "categories are"
        console.print(
            f"[dim]Note:[/dim] {len(removed)} removed {label} hidden by default (use --all to show it)."
        )


def render_frameworks_table(frameworks: Mapping[str, Iterable], *, include_deprecated: bool = False):
    """Render the frameworks by category table (used by `nichebench list frameworks`)."""
    console = Console()
    from rich.text import Text

    splash = Text("NicheBench", style="bold magenta", justify="center")
    splash.append("  —  Framework Packs", style="dim")
    console.print(Panel(splash, style="bold magenta", expand=False, border_style="magenta"))

    table = Table(
        title="[bold cyan]Test Categories by Framework[/bold cyan]",
        box=box.ROUNDED,
        border_style="cyan",
    )
    table.add_column("[bold yellow]Framework[/bold yellow]", style="bold yellow", no_wrap=True)
    table.add_column("[bold green]Category[/bold green]", style="green", no_wrap=True)
    table.add_column("[bold blue]Test Count[/bold blue]", style="bold blue", justify="right")
    table.add_column("[bold white]Framework Total[/bold white]", style="bold white", justify="right")

    all_specs: List = []
    for name, tasklist in frameworks.items():
        specs = sort_task_specs(tasklist, include_deprecated=include_deprecated)
        all_specs.extend(tasklist)
        # tasklist is an iterable of TaskSpec objects
        cat_counts = {t.task_type: len(t.testcases) for t in specs}
        total = sum(cat_counts.values())
        for cat, count in cat_counts.items():
            table.add_row(
                f"[yellow]{name}[/yellow]",
                _category_label(cat),
                f"[bold blue]{count}[/bold blue]",
                f"[white]{total}[/white]",
            )
    console.print(table)
    _print_deprecated_note(console, all_specs, include_deprecated=include_deprecated)


def render_frameworks_overview_table(frameworks: Mapping[str, Iterable], *, include_deprecated: bool = False):
    """Render the discovered frameworks overview table (used by `nichebench list tasks` with no framework).

    Totals follow the same rule as the other listings: removed
    (non-runnable) categories are excluded unless ``include_deprecated``
    is set, so the counts here always match ``list frameworks`` and
    ``list-tasks <framework>`` for the same ``--all`` value.
    """
    console = Console()
    table = Table(
        title="[bold cyan]Discovered Frameworks[/bold cyan]",
        box=box.SIMPLE,
        border_style="cyan",
    )
    table.add_column("[bold yellow]Framework[/bold yellow]", style="bold yellow")
    table.add_column("[bold blue]Total Tasks[/bold blue]", style="bold blue", justify="right")
    all_specs: List = []
    for name, tasklist in frameworks.items():
        specs = sort_task_specs(tasklist, include_deprecated=include_deprecated)
        all_specs.extend(tasklist)
        total = sum(len(t.testcases) for t in specs)
        table.add_row(f"[yellow]{name}[/yellow]", f"[bold blue]{total}[/bold blue]")
    console.print(table)
    _print_deprecated_note(console, all_specs, include_deprecated=include_deprecated)


def render_tasks_for_framework(framework: str, tasklist: Iterable, *, include_deprecated: bool = False):
    """Render the task listing table for a single framework."""
    console = Console()
    specs = sort_task_specs(tasklist, include_deprecated=include_deprecated)
    table = Table(
        title=f"[bold cyan]Tasks in {framework}[/bold cyan]",
        box=box.SIMPLE_HEAVY,
        border_style="cyan",
    )
    table.add_column("[bold green]Category[/bold green]", style="green")
    table.add_column("[bold yellow]Test ID[/bold yellow]", style="yellow")
    table.add_column("[bold white]Summary[/bold white]", style="white")
    for ts in specs:
        for tc in ts.testcases:
            table.add_row(
                _category_label(ts.task_type),
                f"[yellow]{tc.id}[/yellow]",
                (tc.summary or "")[:80],
            )
    console.print(table)
    _print_deprecated_note(console, tasklist, include_deprecated=include_deprecated)


def render_case_panel(framework_name: str, ts, tc) -> None:
    """Render a nicely formatted panel for a single test case (used by `show case`)."""
    if getattr(ts, "task_type", None) == "quiz":
        render_quiz_case_panel(framework_name, ts, tc)
        return

    console = Console()
    body_lines = [
        f"Framework: {framework_name}",
        f"Type: {ts.task_type}",
        f"ID: {tc.id}",
        f"Summary: {tc.summary or ''}",
        "",
        "Context:",
        (tc.context or "")[:1000],
        "",
        "Raw:",
        str(tc.raw),
    ]
    body = "\n".join(body_lines)
    console.print(Panel(body, title=f"{tc.id}"))


def render_quiz_case_panel(framework_name: str, ts, tc) -> None:
    """Render a quiz test case with normalized choices and the correct letter.

    The choices are printed with exactly one letter prefix each (the loader
    already strips the redundant prefixes stored in the dataset), so the
    panel never shows a doubled ``A) A)`` prefix.  The raw mapping is not
    printed for quizzes because it still carries the dataset prefixes.
    """
    console = Console()
    choices = list(tc.choices or [])
    question = ""
    raw = getattr(tc, "raw", None)
    if isinstance(raw, dict):
        question = str(raw.get("question") or "")

    body_lines = [
        f"Framework: {framework_name}",
        f"Type: {ts.task_type}",
        f"ID: {tc.id}",
        f"Summary: {tc.summary or ''}",
        f"Dataset version: {tc.dataset_version or 'unknown'}",
        "",
        "Context:",
        (tc.context or "")[:1000],
        "",
        "Question:",
        question,
        "",
        "Choices:",
    ]

    if choices:
        for index, choice in enumerate(choices):
            letter = chr(ord("A") + index)
            body_lines.append(f"  {letter}) {normalize_choice(choice)}")
    else:
        body_lines.append("  (no choices available for this test case)")

    body_lines.append("")
    body_lines.append(f"Correct answer: {tc.correct_choice or 'unknown'}")

    console.print(Panel("\n".join(body_lines), title=f"{tc.id}"))


def make_frameworks_table():
    table = Table(title="Frameworks")
    table.add_column("framework")
    table.add_column("task_types")
    table.add_column("total_tests", justify="right")
    return table


def render_report_table(summary: dict, title: Optional[str] = None) -> None:
    from rich.console import Console

    # Every category — quiz included — is judged by an LLM, so the summary
    # keys are reported as they are.
    rows = dict(summary)

    table = Table(title=title or "Results Summary")
    for k in rows:
        table.add_column(str(k))
    table.add_row(*[str(rows[k]) for k in rows])
    console = Console()
    console.print(table)
