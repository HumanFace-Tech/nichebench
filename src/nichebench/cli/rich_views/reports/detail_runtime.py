"""Per-test detail table rendering for the ``runtime`` category.

Owner: ``_render_runtime_details_table``.

Renders runtime output in two focused panels:

1. **Narrow primary score table** (~80 cols): Test ID, Gate, Det %, Judge %,
   Hybrid % (or mean±std for multi-trial), Checks passed/total, Trials.
   Failed check names are NOT in this table — they live in panel 2.

2. **Failed Checks panel**: wraps freely; does not crush score visibility.

3. **Provenance & Summary panel**: compact secondary panel.

Supports multi-trial grouping with mean ± stddev display.
"""

import math
from collections import defaultdict
from typing import Mapping, Sequence

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from nichebench.execution.summary import normalize_score


def _render_runtime_details_table(console: Console, header_text: str, rows: Sequence[Mapping]):
    """Render a per-test detail table for the ``runtime`` category.

    Narrow primary score table + separate failed-checks panel so score columns
    are never crushed by long check names.  Supports multi-trial with mean±std.
    """
    # Detect multi-trial: group rows by test_id
    id_groups: dict = defaultdict(list)
    for row in rows:
        id_groups[row.get("test_id", "?")].append(row)

    multi_trial = any(len(v) > 1 for v in id_groups.values())

    # ── Primary score table (narrow, ~80 cols) ─────────────────────────────────
    dtable = Table(show_lines=True, header_style="bold blue", padding=(0, 1))
    # Test ID: truncate at 16, overflows fold
    dtable.add_column("Test", style="yellow", width=16, overflow="fold")
    # Gate: 5 chars (PASS/FAIL)
    dtable.add_column("Gate", style="bold", justify="center", width=5)
    # Det %: 7 chars (e.g. "78.57%")
    dtable.add_column("Det", style="bold", justify="right", width=7)
    # Judge %: 7 chars (e.g. "30.00%" or "N/A")
    dtable.add_column("Judge", style="bold", justify="right", width=7)
    # Hybrid %: 12 chars (e.g. "54.54±4.16%" or "54.54%")
    dtable.add_column("Hybrid", style="bold", justify="right", width=12)
    # Checks passed/total: 7 chars (e.g. "17/21")
    dtable.add_column("Checks", style="cyan", justify="center", width=7)
    # Trials: 3 chars (only when multi-trial)
    if multi_trial:
        dtable.add_column("T", style="dim", justify="center", width=3)

    provenance_rows = []
    failed_checks_by_test: dict = {}

    for test_id, test_rows in id_groups.items():
        # Use the last trial's row for metadata (checks, provenance)
        representative = test_rows[-1]
        judge_output = representative.get("judge_output", {})

        hybrid_scores = []
        det_scores = []
        judge_scores_list = []
        for r in test_rows:
            jo = r.get("judge_output", {})
            hybrid_scores.append(normalize_score(jo.get("hybrid_score"), 0.0))
            det_scores.append(normalize_score(jo.get("deterministic_score"), 0.0))
            js_raw = jo.get("judge_score")
            if js_raw is not None:
                judge_scores_list.append(normalize_score(js_raw, 0.0))

        k = len(hybrid_scores)
        hybrid_mean = sum(hybrid_scores) / k
        det_mean = sum(det_scores) / k
        judge_mean = sum(judge_scores_list) / len(judge_scores_list) if judge_scores_list else None

        if k > 1:
            variance = sum((s - hybrid_mean) ** 2 for s in hybrid_scores) / (k - 1)
            hybrid_std = math.sqrt(variance)
        else:
            hybrid_std = 0.0

        deterministic_gate_passed = judge_output.get("deterministic_gate_passed", representative.get("pass", False))
        checks = judge_output.get("checks", [])

        gate_text = "[bold green]PASS[/bold green]" if deterministic_gate_passed else "[bold red]FAIL[/bold red]"

        # Format deterministic percentage with decimals
        det_pct = det_mean * 100
        if det_pct == 100.0:
            deterministic_text = "100.00%"
        elif det_pct == 0.0:
            deterministic_text = "0.00%"
        else:
            deterministic_text = f"{det_pct:.2f}%"

        # Format judge percentage with decimals
        judge_text = "N/A"
        if judge_mean is not None:
            jp = judge_mean * 100
            if jp == 100.0:
                judge_text = "100.00%"
            elif jp == 0.0:
                judge_text = "0.00%"
            else:
                judge_text = f"{jp:.2f}%"

        # Format hybrid final percentage
        if multi_trial and k > 1:
            hp = hybrid_mean * 100
            sp = hybrid_std * 100
            if hp == 100.0:
                final_text = f"100±{sp:.2f}%"
            elif hp == 0.0:
                final_text = f"0±{sp:.2f}%"
            else:
                final_text = f"{hp:.2f}±{sp:.2f}%"
            trials_text = str(k)
        else:
            hp = hybrid_mean * 100
            if hp == 100.0:
                final_text = "100.00%"
            elif hp == 0.0:
                final_text = "0.00%"
            else:
                final_text = f"{hp:.2f}%"
            trials_text = None

        # Build checks summary: passed/total
        passed_checks = sum(1 for c in checks if c.get("passed"))
        total_checks = len(checks)
        checks_text = f"{passed_checks}/{total_checks}"

        # Collect failed check names for separate panel
        failed_check_names = [c.get("name") for c in checks if not c.get("passed")]
        if failed_check_names:
            failed_checks_by_test[test_id] = failed_check_names

        # Collect provenance for secondary panel
        base_branch = representative.get("base_branch", "?")
        resolved_sha = str(representative.get("resolved_sha", "?"))[:8]
        provenance_rows.append(
            {
                "test_id": test_id,
                "branch": base_branch,
                "sha": resolved_sha,
                "profile": representative.get("effective_profile", "unknown"),
                "summary": representative.get("summary", "No summary"),
            }
        )

        if multi_trial:
            dtable.add_row(
                str(test_id),
                gate_text,
                deterministic_text,
                judge_text,
                final_text,
                checks_text,
                trials_text,
            )
        else:
            dtable.add_row(
                str(test_id),
                gate_text,
                deterministic_text,
                judge_text,
                final_text,
                checks_text,
            )

    console.print("\n")
    console.print(
        Panel(
            dtable,
            title=header_text,
            style="cyan",
            padding=(1, 0, 0, 0),
            expand=False,
            width=80,
        )
    )

    # ── Failed Checks panel (wraps freely, never crushes scores) ───────────────
    if failed_checks_by_test:
        ftable = Table(
            header_style="bold red",
            padding=(0, 1),
            show_lines=True,
            title="[bold red]Failed Checks[/bold red]",
        )
        ftable.add_column("Test", style="yellow", width=16)
        ftable.add_column("Failed Checks", style="red", width=60, overflow="fold")

        for test_id, names in failed_checks_by_test.items():
            ftable.add_row(str(test_id), ", ".join(names))

        console.print(
            Panel(
                ftable,
                style="red",
                padding=(1, 0, 0, 0),
                expand=False,
                width=80,
            )
        )
    else:
        # Zero-failure run: show a brief confirmation
        console.print(
            Panel(
                "[green]All checks passed.[/green]",
                title="[bold green]Failed Checks[/bold green]",
                style="green",
                padding=(1, 0, 0, 0),
                expand=False,
                width=80,
            )
        )

    # ── Provenance & Summary panel ──────────────────────────────────────────────
    if provenance_rows:
        prov_table = Table(header_style="bold dim", padding=(0, 1), show_lines=False)
        prov_table.add_column("Test ID", style="yellow", width=16)
        prov_table.add_column("Branch", style="dim", width=20)
        prov_table.add_column("SHA", style="dim", width=10)
        prov_table.add_column("Profile", style="magenta", width=15)
        prov_table.add_column("Summary", style="dim", width=40, overflow="fold")

        for pr in provenance_rows:
            prov_table.add_row(
                pr["test_id"],
                pr["branch"],
                pr["sha"],
                pr["profile"],
                pr["summary"],
            )

        console.print(
            Panel(
                prov_table,
                title="[dim]Provenance & Summary[/dim]",
                style="dim",
                padding=(1, 0, 0, 0),
                expand=False,
                width=80,
            )
        )
