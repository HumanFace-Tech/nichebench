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

from nichebench.config.nichebench_config import get_config
from nichebench.config.settings import settings
from nichebench.core.discovery import discover_frameworks
from nichebench.metrics.bug_fixing_metric import DeepEvalBugFixingMetric
from nichebench.metrics.code_generation_metric import DeepEvalCodeGenerationMetric
from nichebench.metrics.deepeval_quiz_metric import DeepEvalQuizMetric
from nichebench.providers.agentic_mut_composer import AgenticMUTPromptComposer
from nichebench.providers.conversation_manager import ConversationManager
from nichebench.providers.litellm_client import LiteLLMClient
from nichebench.providers.litellm_judge import LiteLLMJudge
from nichebench.providers.mut_prompt_composer import MUTPromptComposer
from nichebench.utils.io import ensure_results_dir, save_json, save_jsonl

from ..rich_views.run_views import (
    LiveTestRunner,
    render_results_saved,
    render_run_header,
)

app = typer.Typer()
console = Console()


def run_agentic_mut(
    test_case, system_prompt, category, model_str, model_config, timeout, retry_attempts, retry_delay, runner
):
    """Run a multi-turn conversation with the MUT for agentic code generation."""
    # Create MUT client once for all scenarios
    mut_client = LiteLLMClient(timeout=timeout, retry_attempts=retry_attempts, retry_delay=retry_delay)

    # For quiz category, use single-turn (existing behavior)
    if category == "quiz":
        user_input = MUTPromptComposer.compose_prompt(
            test_case=test_case, system_prompt=system_prompt, category=category
        )
        mut_response = mut_client.generate(
            prompt=user_input, model=model_str, model_params=model_config.get("parameters", {})
        )
        return mut_response.get("output", f"[Error: no output from {model_str}]"), user_input

    # For code generation and bug fixing, use multi-turn conversations
    if category == "code_generation":
        conversation = AgenticMUTPromptComposer.start_code_conversation(test_case, system_prompt)
    elif category == "bug_fixing":
        conversation = AgenticMUTPromptComposer.start_bug_conversation(test_case, system_prompt)
    else:
        # Fallback to single-turn for unknown categories
        user_input = MUTPromptComposer.compose_prompt(
            test_case=test_case, system_prompt=system_prompt, category=category
        )
        mut_response = mut_client.generate(
            prompt=user_input, model=model_str, model_params=model_config.get("parameters", {})
        )
        return mut_response.get("output", f"[Error: no output from {model_str}]"), user_input

    # Execute multi-turn conversation

    # Get initial messages
    messages = conversation._format_for_llm()
    turn_count = 0

    while messages and turn_count < conversation.max_turns:
        turn_count += 1
        runner.update_test_status(f"[yellow]🧪 {test_case.id}[/yellow] - MUT Turn {turn_count}...", 1)

        try:
            # Call model with conversation messages
            mut_response = mut_client.generate_with_messages(
                messages=messages, model=model_str, model_params=model_config.get("parameters", {})
            )
            assistant_output = mut_response.get("output", f"[Error: no output from {model_str}]")

            # Check for MUT error
            if "[Error:" in assistant_output:
                return assistant_output, "Multi-turn conversation (see conversation manager for full context)"

            # Add assistant response and check if conversation should continue
            messages = conversation.add_assistant_response(assistant_output)

            # Check if conversation has an error (repetitive/excessive content)
            if hasattr(conversation, "has_error") and conversation.has_error:
                error_msg = f"[Error: Model misbehavior - {conversation.error_reason}]"
                return error_msg, "Multi-turn conversation (model error occurred)"

            # If conversation is complete, break
            if messages is None:
                break

        except Exception as e:
            return f"[Error: Exception in turn {turn_count}: {str(e)}]", "Multi-turn conversation (error occurred)"

    # Extract final answer from conversation
    final_output = (
        conversation.final_answer
        if conversation.is_complete
        else f"[Error: Conversation incomplete after {turn_count} turns]"
    )

    # Create a representation of the original prompt for logging
    initial_user_message = None
    for turn in conversation.turns:
        if turn.role == "user":
            initial_user_message = turn.content
            break

    conversation_summary = conversation.get_conversation_summary()
    print(f"DEBUG: Conversation completed - {conversation_summary}")

    return final_output, initial_user_message or "Multi-turn conversation"


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

    # Use network settings from config, with settings module as fallback
    timeout = network_config.get("timeout", settings.default_timeout)
    retry_attempts = network_config.get("retry_attempts", settings.retry_attempts)
    retry_delay = network_config.get("retry_delay", settings.retry_delay)

    # Create model strings for display
    mut_model_str = config.get_model_string(mut_config)
    judge_model_str = config.get_model_string(judge_config)

    render_run_header(console, mut_model_str, judge_model_str, profile)

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
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, var_name, None)

    system_prompt = import_prompt_var(prompt_mod_path, f"{category.upper()}_SYSTEM_PROMPT")
    judge_system_prompt = import_prompt_var(judge_mod_path, f"JUDGE_{category.upper()}_SYSTEM_PROMPT")

    results = []

    # Use live progress tracking with incremental saving
    with LiveTestRunner(console, framework, category, len(testcases)) as runner:
        for tc in testcases:
            runner.start_test(tc.id)

            try:
                # Step 1: Run MUT with multi-turn conversation
                runner.update_test_status(f"[yellow]🧪 {tc.id}[/yellow] - Running MUT ({mut_model_str})...", 1)

                mut_output, user_input = run_agentic_mut(
                    tc, system_prompt, category, mut_model_str, mut_config, timeout, retry_attempts, retry_delay, runner
                )

                # Check for MUT errors
                if "[Error:" in mut_output:
                    runner.finish_test(tc.id, False, f"MUT failed: {mut_output}")
                    result = {
                        "framework": framework,
                        "category": category,
                        "test_id": tc.id,
                        "summary": getattr(tc, "summary", "") or tc.raw.get("summary", ""),
                        "mut_model": mut_model_str,
                        "judge_model": judge_model_str,
                        "input": user_input,
                        "output": mut_output,
                        "gold": tc.correct_choice or getattr(tc, "checklist", []),
                        "judge_output": {"error": "MUT failed", "raw": mut_output},
                        "pass": False,
                    }
                    results.append(result)

                    # Save incrementally
                    save_jsonl(details_path, [result], mode="a")
                    continue

                # Step 2: Run Judge evaluation
                runner.update_test_status(f"[yellow]🧪 {tc.id}[/yellow] - Running Judge ({judge_model_str})...", 2)

                if category == "quiz":
                    # Create the deepeval metric with configured judge
                    judge_client = LiteLLMClient(
                        timeout=timeout, retry_attempts=retry_attempts, retry_delay=retry_delay
                    )
                    judge_instance = LiteLLMJudge(client=judge_client)
                    metric = DeepEvalQuizMetric(
                        judge=judge_instance,
                        judge_model=judge_model_str,
                        judge_params=judge_config.get("parameters", {}),
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
                        "summary": getattr(tc, "summary", "") or tc.raw.get("summary", ""),
                        "mut_model": mut_model_str,
                        "judge_model": judge_model_str,
                        "input": user_input,
                        "output": mut_output,
                        "gold": tc.correct_choice or getattr(tc, "checklist", []),
                        "judge_output": judge_output,
                        "pass": bool(score),
                    }
                elif category == "code_generation":
                    # Create the code generation metric with configured judge
                    judge_client = LiteLLMClient(
                        timeout=timeout, retry_attempts=retry_attempts, retry_delay=retry_delay
                    )
                    judge_instance = LiteLLMJudge(client=judge_client)
                    metric = DeepEvalCodeGenerationMetric(
                        judge=judge_instance,
                        judge_model=judge_model_str,
                        judge_params=judge_config.get("parameters", {}),
                    )

                    # Build an official deepeval LLMTestCase
                    stc = LLMTestCase(
                        input=user_input,
                        actual_output=mut_output,
                        expected_output="",  # Not used for code generation
                    )

                    # Attach checklist and judge system prompt
                    if hasattr(tc, "checklist") and tc.checklist:
                        setattr(stc, "checklist", tc.checklist)

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
                        "summary": getattr(tc, "summary", "") or tc.raw.get("summary", ""),
                        "mut_model": mut_model_str,
                        "judge_model": judge_model_str,
                        "input": user_input,
                        "output": mut_output,
                        "gold": tc.checklist if hasattr(tc, "checklist") else [],
                        "judge_output": judge_output,
                        "pass": bool(score >= 0.7),  # Default threshold
                    }
                elif category == "bug_fixing":
                    # Create the bug fixing metric with configured judge
                    judge_client = LiteLLMClient(
                        timeout=timeout, retry_attempts=retry_attempts, retry_delay=retry_delay
                    )
                    judge_instance = LiteLLMJudge(client=judge_client)
                    metric = DeepEvalBugFixingMetric(
                        judge=judge_instance,
                        judge_model=judge_model_str,
                        judge_params=judge_config.get("parameters", {}),
                    )
                    # Build an official deepeval LLMTestCase
                    stc = LLMTestCase(
                        input=user_input,
                        actual_output=mut_output,
                        expected_output="",  # Not used for bug fixing
                    )
                    # Attach checklist and judge system prompt
                    if hasattr(tc, "checklist") and tc.checklist:
                        setattr(stc, "checklist", tc.checklist)
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
                        "summary": getattr(tc, "summary", "") or tc.raw.get("summary", ""),
                        "mut_model": mut_model_str,
                        "judge_model": judge_model_str,
                        "input": user_input,
                        "output": mut_output,
                        "gold": tc.checklist if hasattr(tc, "checklist") else [],
                        "judge_output": judge_output,
                        "pass": bool(score >= 0.7),  # Default threshold
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
                        "summary": getattr(tc, "summary", "") or tc.raw.get("summary", ""),
                        "mut_model": mut_model_str,
                        "judge_model": judge_model_str,
                        "input": user_input,
                        "output": mut_output,
                        "gold": tc.correct_choice or getattr(tc, "checklist", []),
                        "judge_output": judge_output,
                        "pass": bool(judge_output["score"]),
                    }

                # Step 2: Save result (no longer shown as separate step since it's instant)
                results.append(result)

                # Save incrementally (append mode)
                save_jsonl(details_path, [result], mode="a")

                # Update summary incrementally
                def categorize_result(r):
                    if category == "code_generation" or category == "bug_fixing":
                        # For code generation, use the score to determine category
                        judge_output = r.get("judge_output", {})
                        if isinstance(judge_output, dict):
                            score = judge_output.get("overall_score", 0.0)
                        else:
                            score = 1.0 if r["pass"] else 0.0

                        if score > 0.66:  # > 66%
                            return "pass"
                        elif score >= 0.33:  # 33-66%
                            return "partial"
                        else:  # < 33%
                            return "fail"
                    else:
                        # For quiz and other categories, use binary pass/fail
                        return "pass" if r["pass"] else "fail"

                # Count results by category
                categorized = [categorize_result(r) for r in results]
                passed_count = sum(1 for c in categorized if c == "pass")
                partial_count = sum(1 for c in categorized if c == "partial")
                failed_count = sum(1 for c in categorized if c == "fail")

                # Calculate average score
                total_score = 0.0
                for r in results:
                    if category in ("code_generation", "bug_fixing"):
                        judge_output = r.get("judge_output", {})
                        if isinstance(judge_output, dict):
                            score = judge_output.get("overall_score", 0.0)
                        else:
                            score = 1.0 if r["pass"] else 0.0
                    else:
                        # For quiz and other categories, binary scoring
                        score = 1.0 if r["pass"] else 0.0
                    total_score += score

                avg_score = total_score / len(results) if results else 0.0

                summary = {
                    "framework": framework,
                    "category": category,
                    "model": mut_model_str,
                    "judge": judge_model_str,
                    "profile": profile,
                    "config": {"mut": mut_config, "judge": judge_config, "evaluation": eval_config},
                    "total": len(results),
                    "passed": passed_count,
                    "partial": partial_count,
                    "failed": failed_count,
                    "avg_score": avg_score,
                }
                save_json(summary_path, summary)

                # Show completion
                runner.finish_test(tc.id, result["pass"])

            except Exception as e:
                # Handle any unexpected errors
                error_msg = str(e)
                runner.finish_test(tc.id, False, error_msg)

                # Save error result
                error_result = {
                    "framework": framework,
                    "category": category,
                    "test_id": tc.id,
                    "summary": getattr(tc, "summary", "") or tc.raw.get("summary", ""),
                    "mut_model": mut_model_str,
                    "judge_model": judge_model_str,
                    "input": getattr(tc, "prompt", "") or str(tc.raw),
                    "output": f"[Error: {error_msg}]",
                    "gold": tc.correct_choice or getattr(tc, "checklist", []),
                    "judge_output": {"error": error_msg},
                    "pass": False,
                }
                results.append(error_result)
                save_jsonl(details_path, [error_result], mode="a")

        # Show final summary
        runner.show_summary()
    # Final results have already been saved incrementally
    # Show report immediately after run
    from ..rich_views.reports import render_run_completion_report

    render_run_completion_report(summary_path, details_path)
    render_results_saved(outdir, console)
