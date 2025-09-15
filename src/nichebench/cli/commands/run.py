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
    ids: str = typer.Option(
        None, "--ids", help="Comma-separated list of specific test IDs to run (e.g., 'code_1,code_2')"
    ),
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

    # Filter test cases by IDs if specified
    if ids:
        requested_ids = [id_str.strip() for id_str in ids.split(",")]
        original_count = len(testcases)
        testcases = [tc for tc in testcases if tc.id in requested_ids]

        # Report on filtering
        if len(testcases) == 0:
            console.print(f"[red]No test cases found with IDs: {', '.join(requested_ids)}[/red]")
            console.print(f"[yellow]Available IDs:[/yellow] {', '.join([tc.id for tc in task.testcases])}")
            raise typer.Exit(1)
        elif len(testcases) < len(requested_ids):
            found_ids = [tc.id for tc in testcases]
            missing_ids = [id for id in requested_ids if id not in found_ids]
            console.print(f"[yellow]Warning: Some IDs not found: {', '.join(missing_ids)}[/yellow]")

        console.print(f"[green]Filtered to {len(testcases)} of {original_count} test cases[/green]")

    # Setup results directory
    details_path, summary_path, outdir = executor.setup_results_directory(results_config)

    # Execute tests with elegant parallel support
    with LiveTestRunner(console, framework, category, len(testcases), parallelism) as runner:
        # Define callbacks for incremental saving and summary updates
        def save_result(result):
            executor.save_incremental_result(result, details_path)

        def update_summary_callback(results):
            executor.update_summary(results, summary_path, profile, eval_config)

        # Execute all tests (sequentially or in parallel based on config)
        executor.execute_tests_parallel(testcases, runner, save_result, update_summary_callback)

        # Show completion summary
        runner.show_summary()

    # Show report immediately after run
    from ..rich_views.reports import render_run_completion_report

    render_run_completion_report(summary_path, details_path)
    render_results_saved(outdir, console)
