"""MVP: Run evals for a framework/category/model, print progress, save results (stub logic)."""
import typer
from pathlib import Path
from datetime import datetime
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.console import Console

import random
import os
import importlib.util

from nichebench.core.discovery import discover_frameworks
from nichebench.utils.io import ensure_results_dir, save_jsonl, save_json

app = typer.Typer()
console = Console()

@app.command()
def all(
    framework: str = typer.Argument(..., help="Framework name"),
    category: str = typer.Argument(..., help="Task category (e.g. quiz, code_generation)"),
    model: str = typer.Option("dummy-model", help="Model name (for results folder)")
):
    """Run all test cases for a framework/category with stub logic."""
    root = Path(__file__).resolve().parents[4] / "src" / "nichebench" / "frameworks"
    frameworks = discover_frameworks(root)
    if framework not in frameworks:
        console.print(f"[red]Framework '{framework}' not found.[/red]")
        raise typer.Exit(1)
    # Find the right TaskSpec
    task = next((t for t in frameworks[framework] if t.task_type == category), None)
    if not task:
        console.print(f"[red]Category '{category}' not found in framework '{framework}'.[/red]")
        raise typer.Exit(2)
    testcases = task.testcases
    if not testcases:
        console.print(f"[yellow]No test cases found for {framework}/{category}.[/yellow]")
        raise typer.Exit(0)
    # Prepare results dir
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = Path("results") / framework / category / model / timestamp
    ensure_results_dir(outdir)
    details_path = outdir / "details.jsonl"
    summary_path = outdir / "summary.json"
    # Import system prompt for MUT
    prompt_mod_path = Path(__file__).resolve().parents[6] / "frameworks" / framework / "prompts" / f"{category.upper()}.py"
    judge_mod_path = Path(__file__).resolve().parents[6] / "frameworks" / framework / "prompts" / "judges" / f"JUDGE_{category.upper()}.py"
    def import_prompt_var(mod_path, var_name):
        if not mod_path.exists():
            return None
        spec = importlib.util.spec_from_file_location("_prompt_mod", str(mod_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, var_name, None)

    system_prompt = import_prompt_var(prompt_mod_path, f"{category.upper()}_SYSTEM_PROMPT")
    judge_system_prompt = import_prompt_var(judge_mod_path, f"JUDGE_{category.upper()}_SYSTEM_PROMPT")

    results = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task(f"[cyan]Running {framework}/{category}", total=len(testcases))
        for tc in testcases:
            # Assemble user input for LLM: system prompt + question/prompt/context
            user_input = ""
            if system_prompt:
                user_input += system_prompt.strip() + "\n\n"
            user_part = tc.prompt or tc.raw.get("question") or tc.context or ""
            user_input += user_part
            # Simulate model output
            mut_output = f"[MUT output for {tc.id}]"
            # Simulate judge output (random pass/fail)
            judge_output = {"score": random.choice([0, 1]), "explanation": "stub", "judge_system_prompt": bool(judge_system_prompt)}
            result = {
                "framework": framework,
                "category": category,
                "test_id": tc.id,
                "mut_model": model,
                "judge_model": "stub-judge",
                "input": user_input,
                "output": mut_output,
                "gold": tc.correct_choice or tc.checklist,
                "judge_output": judge_output,
                "pass": bool(judge_output["score"]),
            }
            results.append(result)
            progress.advance(task_id)
    # Save results
    save_jsonl(details_path, results)
    summary = {
        "framework": framework,
        "category": category,
        "model": model,
        "total": len(results),
        "passed": sum(1 for r in results if r["pass"]),
        "failed": sum(1 for r in results if not r["pass"]),
    }
    save_json(summary_path, summary)
    # Show report immediately after run
    from ..rich_views.reports import render_run_completion_report
    render_run_completion_report(summary_path, details_path)
    console.print(f"[green]Results saved to {outdir}[/green]")
