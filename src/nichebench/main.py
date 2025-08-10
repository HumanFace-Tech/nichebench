"""
NicheBench CLI - LightEval-powered framework for benchmarking AI models
on framework-specific tasks.
"""

import subprocess
from typing import List, Optional

import questionary
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from nichebench.tasks import (
    TASK_REGISTRY,
    get_available_frameworks,
    get_task_sample_count,
    get_tasks_by_category,
    get_tasks_by_framework,
)

app = typer.Typer(
    name="nichebench",
    help=(
        "LightEval-powered CLI framework for benchmarking AI models "
        "on framework-specific tasks"
    ),
    rich_markup_mode="rich",
)
console = Console()

# Provider options for interactive selection
PROVIDERS = {
    "openai": "OpenAI (GPT-4o, GPT-5, etc.)",
    "anthropic": "Anthropic (Claude 4, Claude 3.5 Sonnet)",
    "google": "Google (Gemini 2.5, Gemini 1.5 Pro/Flash)",
    "together": "Together AI (Llama 3.1/3.2, CodeLlama)",
    "groq": "Groq (Fast inference: Llama 3.1, Mixtral)",
    "local": "Local (Ollama: Llama 3.2, Qwen2.5, Mistral)",
}

MODELS = {
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-5", "gpt-4-turbo", "o1-pro"],
    "anthropic": [
        "claude-opus-4.1",
        "claude-sonnet-4",
        "claude-3.5-sonnet",
        "claude-3.5-haiku",
    ],
    "google": [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    ],
    "together": [
        "meta-llama/Llama-3.2-90b-vision-instruct",
        "meta-llama/Llama-3.1-70b-instruct-turbo",
        "meta-llama/CodeLlama-34b-instruct",
        "mistralai/Mixtral-8x22B-instruct",
    ],
    "groq": [
        "llama-3.1-70b-versatile",
        "llama-3.1-8b-instant",
        "mixtral-8x7b-32768",
        "gemma2-9b-it",
    ],
    "local": [
        "llama3.2:3b",
        "llama3.1:8b",
        "qwen2.5:7b",
        "mistral:7b",
        "codellama:13b",
    ],
}


def interactive_provider_model_selection() -> tuple[str, str]:
    """Interactive selection of provider and model."""
    # Select provider
    provider_choices = [f"{key}: {value}" for key, value in PROVIDERS.items()]
    provider_answer = questionary.select(
        "Select AI provider:", choices=provider_choices
    ).ask()

    if not provider_answer:
        console.print("[red]No provider selected. Exiting.[/red]")
        raise typer.Exit(1)

    provider = provider_answer.split(":")[0]

    # Select model
    model_choices = MODELS.get(provider, ["custom"])
    model = questionary.select(
        f"Select model for {provider}:", choices=model_choices
    ).ask()

    if not model:
        console.print("[red]No model selected. Exiting.[/red]")
        raise typer.Exit(1)

    return provider, model


def interactive_task_selection(
    framework: Optional[str] = None,
) -> tuple[str, str, List[str]]:
    """Interactive selection of tasks."""
    if framework:
        # Framework specified, select category
        categories = ["all", "quiz", "code generation"]
        category = questionary.select(
            f"Select category for {framework}:", choices=categories
        ).ask()

        if not category:
            console.print("[red]No category selected. Exiting.[/red]")
            raise typer.Exit(1)

        category_key = "all" if category == "all" else category.split()[0]
        tasks = get_tasks_by_category(framework, category_key)
    else:
        # No framework specified, select framework first
        frameworks = get_available_frameworks()
        if not frameworks:
            console.print("[red]No frameworks found! Please add framework tasks.[/red]")
            raise typer.Exit(1)

        framework = questionary.select("Select framework:", choices=frameworks).ask()

        if not framework:
            console.print("[red]No framework selected. Exiting.[/red]")
            raise typer.Exit(1)

        return interactive_task_selection(framework)

    return framework, category_key, tasks


def run_lighteval_command(
    tasks: List[str],
    provider: str,
    model: str,
    parallel: int,
    output_dir: str,
    dry_run: bool,
) -> int:
    """Build and optionally run the LightEval command.

    Returns process return code (0 on success).
    In dry-run mode, returns 0 after printing.
    """
    # Format tasks for lighteval. The first segment is the suite; we use "community".
    task_specs = [f"community|{task}|0|0" for task in tasks]
    tasks_arg = ",".join(task_specs)

    # Build lighteval command
    cmd = [
        "lighteval",
        "accelerate",
        f"--model_args=pretrained={model}",
        f"--tasks={tasks_arg}",
        "--override_batch_size=1",
        f"--output_dir={output_dir}",
        # Make sure LightEval can import our task table and custom metrics
        "--custom-tasks=nichebench.tasks",
        "--custom-metrics=nichebench.metrics",
    ]

    console.print(f"[green]Prepared command:[/green] {' '.join(cmd)}")
    console.print(f"Tasks: {tasks}")
    console.print(f"Provider: {provider}")
    console.print(f"Model: {model}")
    console.print(f"Parallel: {parallel}")
    console.print(f"Output: {output_dir}")

    if dry_run:
        console.print(
            "[yellow]Dry run: not executing lighteval. Use --execute to run.[/yellow]"
        )
        return 0

    # Execute the command
    try:
        result = subprocess.run(cmd, check=False)
        return result.returncode
    except FileNotFoundError:
        console.print(
            "[red]Error: 'lighteval' not found in PATH. Is it installed?[/red]"
        )
        return 127


@app.command()
def run(
    framework: Optional[str] = typer.Argument(
        None, help="Framework to test (use 'list-tasks' to see available frameworks)"
    ),
    provider: Optional[str] = typer.Option(None, help="AI provider"),
    model: Optional[str] = typer.Option(None, help="Model name"),
    parallel: int = typer.Option(1, help="Parallel jobs"),
    output_dir: str = typer.Option("./results", help="Output directory"),
    execute: bool = typer.Option(
        False,
        "--execute/--dry-run",
        help="Actually run LightEval (default is dry-run that only prints the command)",
    ),
) -> None:
    """Run tasks interactively or with full specification."""
    if not framework:
        # Full interactive mode
        framework, category, tasks = interactive_task_selection()
        if not provider or not model:
            provider, model = interactive_provider_model_selection()
    else:
        # Semi-interactive mode
        tasks = get_tasks_by_framework(framework)
        if not tasks:
            console.print(f"[red]No tasks found for framework '{framework}'[/red]")
            raise typer.Exit(1)

        if not provider or not model:
            provider, model = interactive_provider_model_selection()

    rc = run_lighteval_command(
        tasks, provider, model, parallel, output_dir, dry_run=not execute
    )
    if rc != 0:
        raise typer.Exit(rc)


@app.command()
def list_tasks() -> None:
    """List available benchmark tasks."""
    console.print("[bold cyan]Available Tasks:[/bold cyan]\n")

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Framework", style="dim", width=12)
    table.add_column("Task Name", style="cyan")
    table.add_column("Category", style="green")
    table.add_column("Samples", style="yellow", justify="center", width=8)

    # Auto-discover all frameworks and their tasks
    frameworks = get_available_frameworks()

    if not frameworks:
        console.print("[red]No frameworks found! Please add framework tasks.[/red]")
        return

    for framework in frameworks:
        framework_tasks = get_tasks_by_framework(framework)

        for task in framework_tasks:
            # Infer category from task name
            if "quiz" in task.lower():
                category = "Quiz"
            elif "code" in task.lower() or "generation" in task.lower():
                category = "Code Generation"
            elif "bug" in task.lower() or "fix" in task.lower():
                category = "Bug Fixing"
            else:
                category = "Other"

            # Get sample count for this task
            sample_count = get_task_sample_count(task)

            table.add_row(framework.title(), task, category, str(sample_count))

    console.print(table)


@app.command()
def version() -> None:
    """Show NicheBench version."""
    console.print("[bold green]NicheBench v0.1.0[/bold green]")
    console.print("LightEval-powered framework for framework-specific AI benchmarks")


if __name__ == "__main__":
    app()
