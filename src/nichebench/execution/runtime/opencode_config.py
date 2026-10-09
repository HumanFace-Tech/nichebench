"""OpenCode cage configuration helpers.

Extracts prompt loading, model binding, provider key derivation, and cage
opencode.json generation from the orchestrator.

Provider remapping
-----------------
OpenCode natively supports a fixed set of providers (openai, anthropic, groq, etc.).
For any other provider (e.g. "llamacpp", "openrouter"), the harness remaps it to
"openai" with ``options.baseURL`` set to the provider's API base URL.  The cage
then routes requests through the compatible npm provider package.

Prompt / model binding
----------------------
- System prompt is loaded from ``prompts/executor.yaml`` (cage_opencode_prompt key).
- Model binding is resolved in order: CLI ``--model`` flag > runtime config
  ``runtime_opencode_model`` > MUT provider/model defaults.
- Per-model token limits (``runtime_opencode_model_limits``) override global limits.

Config synthesis purpose
------------------------
``write_cage_opencode_json`` assembles the cage's opencode.json at runtime.
This is the only file the cage reads at startup; it encodes all runtime
decisions (provider, model, token limits, permission grants, system prompt).

Model metadata source
---------------------
``runtime_opencode_model_config_file`` points at a host-side OpenCode config
file (e.g. the project ``opencode.jsonc``) that declares model metadata the
pinned cage runtime does not ship with. Only the selected entry
``provider[<effective provider key>].models[<selected model id>]`` is read,
and only the fields listed in ``SUPPORTED_MODEL_METADATA_FIELDS`` are
propagated. Everything else in that file — permissions, agents, plugins,
provider options, credentials — is discarded so nothing can leak into the
cage. Runtime token limits are applied on top of the imported entry.

Ownership boundaries
-------------------
- Does NOT own workspace lifecycle (see ``workspace.py``)
- Does NOT own trajectory building (see ``trajectory.py``)
- Does NOT own artifact persistence (see ``artifacts.py``)
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from nichebench.core.prompt_loader import load_prompt_text

# Path to executor prompts YAML
PROMPTS_PATH = Path(__file__).resolve().parent / "prompts" / "executor.yaml"

# Providers that OpenCode recognises natively. Any other provider (e.g.
# "llamacpp") must be remapped to "openai" with options.baseURL when an
# api_base is configured.
OPENCODE_NATIVE_PROVIDERS: frozenset[str] = frozenset(
    {
        "openai",
        "anthropic",
        "groq",
        "google",
        "vertex",
        "bedrock",
        "azure",
        "deepseek",
        "xai",
        "togetherai",
        "mistral",
        "cloudflare",
        "ollama",
        "cerebras",
    }
)

SUPPORTED_MODEL_METADATA_FIELDS: tuple[str, ...] = (
    "id",
    "name",
    "family",
    "release_date",
    "attachment",
    "reasoning",
    "temperature",
    "tool_call",
    "interleaved",
    "cost",
    "limit",
    "modalities",
    "experimental",
    "status",
)


def load_review_nudge() -> str:
    """Load review nudge text from executor.yaml."""
    return (
        load_prompt_text(
            PROMPTS_PATH,
            "cage_opencode_review_nudge",
            default="",
        )
        or ""
    )


def derive_cage_npm_provider_key(opencode_provider: str, runtime_config: Dict[str, Any]) -> str:
    """Derive the provider key used in opencode.json and --model flag for npm-based cage runs.

    Uses ``runtime_opencode_provider_name`` when set, otherwise sanitizes the
    original provider name to a key-safe string.
    """
    explicit = runtime_config.get("runtime_opencode_provider_name")
    if explicit:
        return str(explicit)
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "-", opencode_provider).strip("-")
    return sanitized or "openai-compat"


def compute_opencode_model_binding(
    mut_provider: str,
    mut_model: str,
    runtime_config: Dict[str, Any],
    cli_model_override: Optional[str] = None,
) -> Tuple[str, str]:
    """Compute OpenCode model binding from MUT provider/model.

    Args:
        mut_provider: MUT provider (e.g., "groq", "openai")
        mut_model: MUT model name (e.g., "gemma2-9b-it", "openai/gpt-oss-120b")
        runtime_config: Runtime configuration dict
        cli_model_override: If set, the raw --model CLI arg was provided
            explicitly and takes precedence over ``runtime_opencode_model``
            in the config.

    Returns:
        Tuple of (provider, model_id) for OpenCode binding
    """
    # Check for explicit override — skipped when the CLI --model flag was
    # provided, because user intent (CLI) must win over static config.
    if cli_model_override is None:
        override_model = runtime_config.get("runtime_opencode_model")
        if override_model:
            if "/" in override_model:
                provider, model_id = override_model.split("/", 1)
                return provider.strip(), model_id.strip()
            # Override without provider defaults to MUT provider
            return mut_provider, override_model.strip()

    provider = mut_provider
    model_id = mut_model

    model_aliases = runtime_config.get("runtime_opencode_model_aliases")
    if isinstance(model_aliases, dict) and model_id in model_aliases:
        model_id = str(model_aliases[model_id])

    return provider, model_id


def get_provider_api_keys(provider: str) -> Dict[str, str]:
    """Get provider API keys from host environment.

    Args:
        provider: Provider name (e.g., "groq", "openai", "anthropic")

    Returns:
        Dict of env var name -> value for API keys that exist in host env
    """
    provider_env_map = {
        "groq": "GROQ_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "xai": "XAI_API_KEY",
        "minimax": "MINIMAX_API_KEY",
    }

    api_keys = {}
    env_var = provider_env_map.get(provider.lower())
    if env_var:
        env_value = os.environ.get(env_var)
        if env_value:
            api_keys[env_var] = env_value

    return api_keys


def read_workspace_system_prompt(workspace_path: Path) -> Optional[str]:
    """Extract ``mode.build.prompt`` from workspace ``opencode.json``."""
    opencode_json_path = workspace_path / "opencode.json"
    if not opencode_json_path.exists():
        return None

    try:
        config = json.loads(opencode_json_path.read_text(encoding="utf-8"))
        if isinstance(config, dict):
            mode = config.get("mode", {})
            if isinstance(mode, dict):
                build = mode.get("build", {})
                if isinstance(build, dict):
                    return build.get("prompt")
    except Exception:
        pass

    return None


def load_model_metadata_from_config_file(
    config_file: str,
    effective_provider_key: str,
    opencode_model_id: str,
) -> Dict[str, Any]:
    """Load model metadata for one model from a host-side OpenCode config file.

    Reads only ``provider[<effective_provider_key>].models[<opencode_model_id>]``
    and keeps only fields in :data:`SUPPORTED_MODEL_METADATA_FIELDS`. The path is
    resolved independently of the task workspace so a project-level declaration
    can be imported without being mounted into the cage.

    Raises:
        ValueError: If the file is missing, is not valid JSON, is not a JSON
            object, or lacks the expected provider/model structure.
    """
    source = Path(config_file).expanduser()
    if not source.is_absolute():
        source = (Path.cwd() / source).resolve()

    if not source.is_file():
        raise ValueError(f"runtime_opencode_model_config_file not found: {source}")

    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"Could not read runtime_opencode_model_config_file {source}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"runtime_opencode_model_config_file {source} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"runtime_opencode_model_config_file {source} must contain a JSON object at the root")

    providers = raw.get("provider")
    if not isinstance(providers, dict):
        raise ValueError(f"runtime_opencode_model_config_file {source} has no 'provider' object")

    provider_block = providers.get(effective_provider_key)
    if not isinstance(provider_block, dict):
        raise ValueError(
            f"runtime_opencode_model_config_file {source} has no provider entry for '{effective_provider_key}'"
        )

    models = provider_block.get("models")
    if not isinstance(models, dict):
        raise ValueError(
            f"runtime_opencode_model_config_file {source} provider '{effective_provider_key}' has no 'models' object"
        )

    entry = models.get(opencode_model_id)
    if not isinstance(entry, dict):
        raise ValueError(
            f"runtime_opencode_model_config_file {source} provider '{effective_provider_key}' "
            f"has no model entry for '{opencode_model_id}'"
        )

    metadata = copy.deepcopy(entry)
    return {key: value for key, value in metadata.items() if key in SUPPORTED_MODEL_METADATA_FIELDS}


def write_cage_opencode_json(
    workspace_host_path: Path,
    opencode_provider: str,
    opencode_model_id: str,
    api_base: Optional[str] = None,
    runtime_config: Optional[Dict[str, Any]] = None,
) -> Path:
    """Write cage-run opencode.json in workspace root."""
    cfg = runtime_config or {}
    prompt = load_prompt_text(
        PROMPTS_PATH,
        "cage_opencode_prompt",
        default="",
    )

    # Compute optional model token limits.
    context_limit = cfg.get("runtime_opencode_context_limit")
    output_limit = cfg.get("runtime_opencode_output_limit")

    # Optional per-model limits override global limits when provided.
    # Example:
    # runtime_opencode_model_limits:
    #   qwen3.5-9b: {context: 262144, output: 131072}
    per_model_limits = cfg.get("runtime_opencode_model_limits")
    if isinstance(per_model_limits, dict):
        entry = per_model_limits.get(opencode_model_id)
        if isinstance(entry, dict):
            model_context = entry.get("context")
            model_output = entry.get("output")
            model_output_ratio = entry.get("output_ratio")
            if model_context is not None:
                context_limit = int(model_context)
            if model_output is not None:
                output_limit = int(model_output)
            elif model_output_ratio is not None and context_limit is not None:
                output_limit = int(int(context_limit) * float(model_output_ratio))
    if context_limit is not None and output_limit is None:
        ratio = float(cfg.get("runtime_opencode_output_ratio", 0.5))
        output_limit = int(int(context_limit) * ratio)

    effective_key = derive_cage_npm_provider_key(opencode_provider, cfg) if api_base else opencode_provider

    model_entry: Dict[str, Any] = {}
    model_config_file = cfg.get("runtime_opencode_model_config_file")
    if model_config_file:
        model_entry = load_model_metadata_from_config_file(
            str(model_config_file),
            effective_key,
            opencode_model_id,
        )

    if context_limit is not None or output_limit is not None:
        limit: Dict[str, Any] = {}
        if context_limit is not None:
            limit["context"] = int(context_limit)
        if output_limit is not None:
            limit["output"] = int(output_limit)
        merged_limit = dict(model_entry["limit"]) if isinstance(model_entry.get("limit"), dict) else {}
        merged_limit.update(limit)
        model_entry["limit"] = merged_limit

    if api_base:
        # npm-based @ai-sdk/openai-compatible provider block.
        npm = str(cfg.get("runtime_opencode_provider_npm", "@ai-sdk/openai-compatible"))
        provider_options: Dict[str, Any] = {"baseURL": api_base}

        timeout_ms = cfg.get("runtime_opencode_timeout_ms")
        chunk_timeout_ms = cfg.get("runtime_opencode_chunk_timeout_ms")
        if timeout_ms is not None:
            provider_options["timeout"] = int(timeout_ms)
        if chunk_timeout_ms is not None:
            provider_options["chunkTimeout"] = int(chunk_timeout_ms)

        if cfg.get("runtime_opencode_set_cache_key"):
            provider_options["setCacheKey"] = True

        provider_block: Dict[str, Any] = {
            "name": effective_key,
            "npm": npm,
            "models": {opencode_model_id: model_entry},
            "options": provider_options,
        }
    else:
        # Native provider — simple model registration.
        provider_block = {"models": {opencode_model_id: model_entry}}

    # Optional compaction block.
    compaction_block: Dict[str, Any] = {}
    compaction_auto = cfg.get("runtime_opencode_compaction_auto")
    compaction_prune = cfg.get("runtime_opencode_compaction_prune")
    compaction_reserved = cfg.get("runtime_opencode_compaction_reserved")
    if compaction_auto is not None:
        compaction_block["auto"] = bool(compaction_auto)
    if compaction_prune is not None:
        compaction_block["prune"] = bool(compaction_prune)
    if compaction_reserved is not None:
        compaction_block["reserved"] = int(compaction_reserved)

    model_binding = f"{effective_key}/{opencode_model_id}"
    config: Dict[str, Any] = {
        "$schema": "https://opencode.ai/config.schema.json",
        "model": model_binding,
        "small_model": model_binding,
        "default_agent": "build",
        "permission": {
            "*": "deny",
            "bash": "allow",
            "read": "allow",
            "edit": "allow",
            "glob": "allow",
            "grep": "allow",
            "write": "allow",
            "list": "allow",
            "patch": "allow",
            "todowrite": "allow",
            "todoread": "allow",
            "question": "deny",
            "task": "deny",
            "skill": "deny",
            "webfetch": "deny",
            "websearch": "deny",
            "external_directory": {
                str(workspace_host_path): "allow",
                "/tmp": "allow",
                "/tmp/opencode": "allow",
                "/nichebench/islands": "allow",
                "/nichebench/state": "allow",
            },
        },
        "mode": {
            "build": {
                "prompt": prompt,
            }
        },
        "agent": {
            "build": {
                "prompt": prompt,
            }
        },
        "provider": {
            effective_key: provider_block,
        },
    }
    if compaction_block:
        config["compaction"] = compaction_block

    out_path = workspace_host_path / "opencode.json"
    out_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return out_path
