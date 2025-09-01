"""MVP: Run evals for a framework/category/model, print progress, save results (stub logic)."""

import importlib.util
import os
import random
from datetime import datetime
from pathlib import Path

import typer
from deepeval.test_case import LLMTestCase
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from nichebench.config.nichebench_config import get_config
from nichebench.core.discovery import discover_frameworks
from nichebench.metrics.deepeval_quiz_metric import DeepEvalQuizMetric
from nichebench.providers.litellm_client import LiteLLMClient
from nichebench.providers.litellm_judge import LiteLLMJudge
from nichebench.providers.mut_prompt_composer import MUTPromptComposer
from nichebench.utils.io import ensure_results_dir, save_json, save_jsonl

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
    results_config = config.get_results_config()

    # Create model strings for display
    mut_model_str = config.get_model_string(mut_config)
    judge_model_str = config.get_model_string(judge_config)

    console.print(f"[cyan]Using MUT:[/cyan] {mut_model_str}")
    console.print(f"[cyan]Using Judge:[/cyan] {judge_model_str}")
    if profile:
        console.print(f"[cyan]Profile:[/cyan] {profile}")

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
    timestamp = datetime.now().strftime(results_config["timestamp_format"])
    outdir = Path("results") / framework / category / mut_model_str.replace("/", "-") / timestamp
    ensure_results_dir(outdir)
    details_path = outdir / "details.jsonl"
    summary_path = outdir / "summary.json"
    # Import system prompt for MUT
    prompt_mod_path = (
        Path(__file__).resolve().parents[6] / "frameworks" / framework / "prompts" / f"{category.upper()}.py"
    )
    judge_mod_path = (
        Path(__file__).resolve().parents[6]
        / "frameworks"
        / framework
        / "prompts"
        / "judges"
        / f"JUDGE_{category.upper()}.py"
    )

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
            # Compose proper prompt using the MUT prompt composer
            user_input = MUTPromptComposer.compose_prompt(test_case=tc, system_prompt=system_prompt, category=category)

            # Call the model-under-test to get actual output
            mut_client = LiteLLMClient()
            mut_response = mut_client.generate(
                prompt=user_input, model=mut_model_str, model_params=mut_config.get("parameters", {})
            )
            mut_output = mut_response.get("output", f"[Error: no output from {mut_model_str}]")
            # If quiz category, use deepeval-backed judge metric
            if category == "quiz":
                # Create the deepeval metric with configured judge
                judge_client = LiteLLMClient()
                judge_instance = LiteLLMJudge(client=judge_client)
                metric = DeepEvalQuizMetric(
                    judge=judge_instance, judge_model=judge_model_str, judge_params=judge_config.get("parameters", {})
                )
                # Build an official deepeval LLMTestCase so metrics receive
                # the expected structure (input, actual_output, expected_output)
                stc = LLMTestCase(
                    input=user_input,
                    actual_output=mut_output,
                    expected_output=tc.correct_choice or "",
                )
                # Attach judge_system_prompt so the metric/judge can use it when
                # building the judge prompt. This is the documented pattern: the
                # LLMTestCase can hold additional metadata that metrics may consult.
                if judge_system_prompt:
                    setattr(stc, "judge_system_prompt", judge_system_prompt)

                score = metric.measure(stc)
                judge_output = (
                    metric.last_judge_response
                    if hasattr(metric, "last_judge_response")
                    else {"score": score, "explanation": "No detailed explanation available"}
                )
                result = {
                    "framework": framework,
                    "category": category,
                    "test_id": tc.id,
                    "mut_model": mut_model_str,
                    "judge_model": judge_model_str,
                    "input": user_input,
                    "output": mut_output,
                    "gold": tc.correct_choice or tc.checklist,
                    "judge_output": judge_output,
                    "pass": bool(score),
                }
            else:
                # Non-quiz categories keep stubbed judge behavior for now
                judge_output = {
                    "score": random.choice([0, 1]),
                    "explanation": "stub",
                    "judge_system_prompt": bool(judge_system_prompt),
                }
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
        "model": mut_model_str,
        "judge": judge_model_str,
        "profile": profile,
        "config": {"mut": mut_config, "judge": judge_config, "evaluation": eval_config},
        "total": len(results),
        "passed": sum(1 for r in results if r["pass"]),
        "failed": sum(1 for r in results if not r["pass"]),
    }
    save_json(summary_path, summary)
    # Show report immediately after run
    from ..rich_views.reports import render_run_completion_report

    render_run_completion_report(summary_path, details_path)
    console.print(f"[green]Results saved to {outdir}[/green]")
