from pathlib import Path


def find_all_run_dirs(results_root: Path = Path("results")):
    """Yield (framework, task, model, timestamp, path) for all runs found."""
    for fw_dir in results_root.iterdir():
        if not fw_dir.is_dir():
            continue
        for task_dir in fw_dir.iterdir():
            if not task_dir.is_dir():
                continue
            for model_dir in task_dir.iterdir():
                if not model_dir.is_dir():
                    continue
                for ts_dir in model_dir.iterdir():
                    if not ts_dir.is_dir():
                        continue
                    yield (
                        fw_dir.name,
                        task_dir.name,
                        model_dir.name,
                        ts_dir.name,
                        ts_dir,
                    )
