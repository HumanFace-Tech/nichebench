"""Show a single test case by id."""
import typer
from pathlib import Path
from rich.console import Console
from rich.panel import Panel

from nichebench.core.discovery import discover_frameworks

app = typer.Typer()
console = Console()


@app.command()
def case(test_id: str = typer.Argument(..., help="Test case id")):
    """Show the raw YAML-parsed test case for a given id."""
    root = Path(__file__).resolve().parents[4] / "src" / "nichebench" / "frameworks"
    frameworks = discover_frameworks(root)
    found = None
    for framework_name, tasklist in frameworks.items():
        for ts in tasklist:
            for tc in ts.testcases:
                if tc.id == test_id:
                    found = (framework_name, ts, tc)
                    break
            if found:
                break
        if found:
            break
    if not found:
        console.print(f"[red]Test case '{test_id}' not found.[/red]")
        raise typer.Exit(code=2)
    framework_name, ts, tc = found
    body = f"Framework: {framework_name}\nType: {ts.task_type}\nID: {tc.id}\nSummary: {tc.summary or ''}\n\nContext:\n{(tc.context or '')[:1000]}\n\nRaw:\n{tc.raw}"
    console.print(Panel(body, title=f"{tc.id}"))
