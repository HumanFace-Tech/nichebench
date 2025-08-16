#!/usr/bin/env python3
"""
Create HuggingFace-compatible datasets from YAML files for LightEval consumption.
"""

import glob
import json
import shutil
import sys
from pathlib import Path

import yaml  # type: ignore

# Some external packages may not have type stubs in this environment; mypy may
# complain. Use type: ignore on these imports to keep local checks passing.
from datasets import Dataset, Features, Sequence, Value, load_dataset  # type: ignore

sys.path.append("src")


def load_yaml_directly(base_dir: Path, category: str) -> list[dict]:
    """Load YAML files directly without using data_loader Doc creation."""
    category_dir = base_dir / category
    if not category_dir.exists():
        return []

    samples = []
    for yaml_file in sorted(category_dir.glob("*.yaml")):
        with open(yaml_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            if data:
                samples.append(data)
    return samples


def create_datasets() -> None:
    """Create HuggingFace-compatible parquet datasets from our YAML files."""
    data_dir = Path("src/nichebench/tasks/drupal/data")
    datasets_dir = Path("datasets")
    datasets_dir.mkdir(exist_ok=True)

    def clean_dataset_dir(d: Path) -> None:
        """Remove artifacts from previous save_to_disk or partial runs."""
        if not d.exists():
            return
        patterns = [
            "state.json",
            "dataset_info.json",
            "dataset_dict.json",
            "*.arrow",
            "train.jsonl",
            "test.jsonl",
        ]
        for p in patterns:
            for f in d.glob(p):
                try:
                    if f.is_dir():
                        shutil.rmtree(f)
                    else:
                        f.unlink()
                except Exception:
                    print(f"warn: failed to remove {f}")

    def write_and_validate_parquet(
        rows: list[dict],
        out_dir: Path,
        task_name: str,
        features: Features | None = None,
    ) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        clean_dataset_dir(out_dir)
        ds = Dataset.from_list(rows)
        if features is not None:
            try:
                ds = ds.cast(features)
            except Exception as e:
                print(f"ERROR: failed to cast dataset for {task_name}: {e}")
                raise
        out_file = out_dir / "train.parquet"
        ds.to_parquet(out_file)
        print(f"✅ Created {task_name} dataset: {len(rows)} samples -> {out_file}")

        # quick validation: try load_dataset on that directory
        try:
            loaded = load_dataset(str(out_dir))
            print(f"   validated: splits={list(loaded.keys())}")
        except Exception as e:
            print(f"ERROR: validation failed for {task_name}: {e}")
            raise

    # Create quiz dataset as parquet
    quiz_samples = []
    for raw_data in load_yaml_directly(data_dir, "quiz"):
        # Fix gold_index based on correct_choice
        correct_choice = str(raw_data.get("correct_choice", "A")).strip().upper()
        mapping = {"A": 0, "B": 1, "C": 2, "D": 3}
        gold_index = mapping.get(correct_choice, 0)

        # minimal schema enforcement
        choices = raw_data.get("choices") or []
        if not isinstance(choices, list):
            # try to coerce
            choices = [str(choices)]

        quiz_samples.append(
            {
                "doc_id": raw_data.get("id") or raw_data.get("doc_id") or "",
                "question": raw_data.get("question", ""),
                "choices": choices,
                "correct_choice": correct_choice,
                "context": raw_data.get("context", ""),
                "gold_index": int(gold_index),
            }
        )

    if quiz_samples:
        quiz_dir = datasets_dir / "drupal_quiz"
        quiz_features = Features(
            {
                "doc_id": Value("string"),
                "question": Value("string"),
                "choices": Sequence(Value("string")),
                "correct_choice": Value("string"),
                "context": Value("string"),
                "gold_index": Value("int64"),
            }
        )
        write_and_validate_parquet(
            quiz_samples, quiz_dir, "quiz", features=quiz_features
        )

    # Create code generation dataset as parquet
    code_samples = []
    for raw_data in load_yaml_directly(data_dir, "code_generation"):
        code_samples.append(
            {
                "doc_id": raw_data.get("id") or raw_data.get("doc_id") or "",
                "prompt": raw_data.get("prompt", ""),
                "context": raw_data.get("context", ""),
                "judge_checklist": raw_data.get("judge_checklist", []) or [],
                "reference": raw_data.get("reference", ""),
            }
        )

    if code_samples:
        code_dir = datasets_dir / "drupal_code_generation"
        code_features = Features(
            {
                "doc_id": Value("string"),
                "prompt": Value("string"),
                "context": Value("string"),
                "judge_checklist": Sequence(Value("string")),
                "reference": Value("string"),
            }
        )
        write_and_validate_parquet(
            code_samples, code_dir, "code_generation", features=code_features
        )

    # Create bug fixing dataset as parquet
    bug_samples = []
    for raw_data in load_yaml_directly(data_dir, "bug_fixing"):
        bug_samples.append(
            {
                "doc_id": raw_data.get("id") or raw_data.get("doc_id") or "",
                "prompt": raw_data.get("prompt", ""),
                "context": raw_data.get("context", ""),
                "judge_checklist": raw_data.get("judge_checklist", []) or [],
                "reference": raw_data.get("reference", ""),
            }
        )

    if bug_samples:
        bug_dir = datasets_dir / "drupal_bug_fixing"
        bug_features = Features(
            {
                "doc_id": Value("string"),
                "prompt": Value("string"),
                "context": Value("string"),
                "judge_checklist": Sequence(Value("string")),
                "reference": Value("string"),
            }
        )
        write_and_validate_parquet(
            bug_samples, bug_dir, "bug_fixing", features=bug_features
        )


if __name__ == "__main__":
    create_datasets()
