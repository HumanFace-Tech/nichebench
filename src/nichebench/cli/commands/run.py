"""MVP: Run evals for a framework/category/model, print progress, save results."""

from datetime import datetime
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console

from nichebench.config.nichebench_config import get_config
from nichebench.core.discovery import discover_frameworks
from nichebench.core.executor import TestExecutor

from ..rich_views.run_views import (
    LiveTestRunner,
    render_results_saved,
    render_run_header,
)

app = typer.Typer()
console = Console()


@app.command()
def all(
    framework: str = typer.Argument(..., help="Framework name"),
    category: str = typer.Argument(..., help="Task category (e.g. quiz, code_generation)"),
    model: str = typer.Option(None, "--model", "-m", help="Override MUT model (e.g., 'groq/gemma2-9b-it')"),
    judge: str = typer.Option(None, "--judge", "-j", help="Override judge model (e.g., 'openai/gpt-5')"),
    profile: str = typer.Option(None, "--profile", "-p", help="Configuration profile to use"),
):
    """Run all test cases for a framework/category with configuration-driven models."""
    # Load environment variables from .env file
    load_dotenv()

    # Load configuration
    config = get_config()

    # Get model configurations with CLI overrides
    mut_config = config.get_mut_config(model_override=model, profile=profile)
    judge_config = config.get_judge_config(judge_override=judge, profile=profile)
    eval_config = config.get_evaluation_config()
    network_config = config.get_network_config()
    results_config = config.get_results_config()

    # Extract parallelism setting
    parallelism = eval_config.get("parallelism", 1)

    # Create test executor with parallelism support
    executor = TestExecutor(framework, category, mut_config, judge_config, network_config, parallelism)

    render_run_header(console, executor.mut_model_str, executor.judge_model_str, profile)

    # Discover and validate framework/category
    root = Path(__file__).resolve().parents[4] / "src" / "nichebench" / "frameworks"
    frameworks = discover_frameworks(root)
    if framework not in frameworks:
        console.print(f"[red]Framework '{framework}' not found.[/red]")
        raise typer.Exit(1)

    task = next((t for t in frameworks[framework] if t.task_type == category), None)
    if not task:
        console.print(f"[red]Category '{category}' not found in framework '{framework}'.[/red]")
        raise typer.Exit(2)

    testcases = task.testcases
    if not testcases:
        console.print(f"[yellow]No test cases found for {framework}/{category}.[/yellow]")
        raise typer.Exit(0)

    # Setup results directory
    details_path, summary_path, outdir = executor.setup_results_directory(results_config)

    # Execute tests with elegant parallel support
    with LiveTestRunner(console, framework, category, len(testcases), parallelism) as runner:
        # Execute all tests (sequentially or in parallel based on config)
        results = executor.execute_tests_parallel(testcases, runner)

        # Save all results incrementally
        for result in results:
            executor.save_incremental_result(result, details_path)

        # Update final summary
        executor.update_summary(results, summary_path, profile, eval_config)

        # Show completion summary
        runner.show_summary()

    # Show report immediately after run
    from ..rich_views.reports import render_run_completion_report

    render_run_completion_report(summary_path, details_path)
    render_results_saved(outdir, console)
