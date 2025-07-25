"""
NicheBench CLI - LightEval-powered framework for benchmarking AI models
on framework-specific tasks.
"""

from typing import List, Optional

import questionary
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from nichebench.tasks import (
    get_available_tasks,
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
    "openai": "OpenAI (GPT-3.5, GPT-4, etc.)",
    "anthropic": "Anthropic (Claude)",
    "together": "Together AI (Llama, Code Llama)",
    "google": "Google (Gemini)",
    "local": "Local (Ollama, vLLM)",
}

MODELS = {
    "openai": ["gpt-3.5-turbo", "gpt-4", "gpt-4-turbo"],
    "anthropic": ["claude-3-sonnet", "claude-3-haiku", "claude-3-opus"],
    "together": [
        "meta-llama/Llama-2-70b-chat-hf",
        "codellama/CodeLlama-34b-Instruct-hf",
    ],
    "google": ["gemini-pro", "gemini-pro-vision"],
    "local": ["llama2", "codellama", "mistral"],
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
        frameworks = ["drupal", "wordpress"]
        framework = questionary.select("Select framework:", choices=frameworks).ask()

        if not framework:
            console.print("[red]No framework selected. Exiting.[/red]")
            raise typer.Exit(1)

        return interactive_task_selection(framework)

    return framework, category_key, tasks


def run_lighteval_command(
    tasks: List[str], provider: str, model: str, parallel: int, output_dir: str
) -> None:
    """Run the actual lighteval command."""
    # Format tasks for lighteval
    task_specs = []
    for task in tasks:
        task_specs.append(f"nichebench|{task}|0|0")

    tasks_arg = ",".join(task_specs)

    # Build lighteval command
    cmd = [
        "lighteval",
        "accelerate",
        f"--model_args=pretrained={model}",
        f"--tasks={tasks_arg}",
        "--override_batch_size=1",
        f"--output_dir={output_dir}",
        "--custom_tasks=nichebench.tasks",
    ]

    console.print(f"[green]Running:[/green] {' '.join(cmd)}")

    # For now, just show what would be run
    console.print(
        "[yellow]Note: LightEval integration is in progress. This would run:[/yellow]"
    )
    console.print(f"Tasks: {tasks}")
    console.print(f"Provider: {provider}")
    console.print(f"Model: {model}")
    console.print(f"Parallel: {parallel}")
    console.print(f"Output: {output_dir}")


@app.command()
def drupal(
    category: str = typer.Option("all", help="Category: all, quiz, code"),
    provider: Optional[str] = typer.Option(None, help="AI provider"),
    model: Optional[str] = typer.Option(None, help="Model name"),
    parallel: int = typer.Option(1, help="Parallel jobs"),
    output_dir: str = typer.Option("./results", help="Output directory"),
) -> None:
    """Run Drupal framework tasks."""
    console.print(
        Panel(
            f"[bold blue]NicheBench - Drupal Tasks[/bold blue]\n"
            f"Category: [cyan]{category}[/cyan]",
            title="Framework Benchmark",
        )
    )

    # Get tasks for the specified category
    tasks = get_tasks_by_category("drupal", category)
    if not tasks:
        console.print(f"[red]No tasks found for category '{category}'[/red]")
        raise typer.Exit(1)

    # Get provider and model (interactive if not specified)
    if not provider or not model:
        provider, model = interactive_provider_model_selection()

    run_lighteval_command(tasks, provider, model, parallel, output_dir)


@app.command()
def wordpress(
    category: str = typer.Option("all", help="Category: all, quiz, code"),
    provider: Optional[str] = typer.Option(None, help="AI provider"),
    model: Optional[str] = typer.Option(None, help="Model name"),
    parallel: int = typer.Option(1, help="Parallel jobs"),
    output_dir: str = typer.Option("./results", help="Output directory"),
) -> None:
    """Run WordPress framework tasks."""
    console.print(
        Panel(
            f"[bold blue]NicheBench - WordPress Tasks[/bold blue]\n"
            f"Category: [cyan]{category}[/cyan]",
            title="Framework Benchmark",
        )
    )

    # Get tasks for the specified category
    tasks = get_tasks_by_category("wordpress", category)
    if not tasks:
        console.print(f"[red]No tasks found for category '{category}'[/red]")
        raise typer.Exit(1)

    # Get provider and model (interactive if not specified)
    if not provider or not model:
        provider, model = interactive_provider_model_selection()

    run_lighteval_command(tasks, provider, model, parallel, output_dir)


@app.command()
def run(
    framework: Optional[str] = typer.Argument(
        None, help="Framework to test (drupal, wordpress)"
    ),
    provider: Optional[str] = typer.Option(None, help="AI provider"),
    model: Optional[str] = typer.Option(None, help="Model name"),
    parallel: int = typer.Option(1, help="Parallel jobs"),
    output_dir: str = typer.Option("./results", help="Output directory"),
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

    run_lighteval_command(tasks, provider, model, parallel, output_dir)


@app.command()
def list_tasks() -> None:
    """List available benchmark tasks."""
    console.print("[bold cyan]Available Tasks:[/bold cyan]\n")

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Framework", style="dim", width=12)
    table.add_column("Task Name", style="cyan")
    table.add_column("Category", style="green")

    drupal_tasks = get_tasks_by_framework("drupal")
    wordpress_tasks = get_tasks_by_framework("wordpress")

    for task in drupal_tasks:
        category = "Quiz" if "quiz" in task else "Code Generation"
        table.add_row("Drupal", task, category)

    for task in wordpress_tasks:
        category = "Quiz" if "quiz" in task else "Code Generation"
        table.add_row("WordPress", task, category)

    console.print(table)


@app.command()
def version() -> None:
    """Show NicheBench version."""
    console.print("[bold green]NicheBench v0.1.0[/bold green]")
    console.print("LightEval-powered framework for framework-specific AI benchmarks")


if __name__ == "__main__":
    app()
