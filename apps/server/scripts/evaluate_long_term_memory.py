from __future__ import annotations

import argparse
from pathlib import Path

from trpg_server.memory.evaluation import (
    evaluate_memory_dataset,
    load_memory_evaluation_dataset,
    report_as_json,
)


DEFAULT_DATASET = (
    Path(__file__).resolve().parents[3]
    / "evals"
    / "long-term-memory"
    / "gray-harbor-v1.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="运行《灰港》长期记忆确定性评估")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    arguments = parser.parse_args()
    dataset = load_memory_evaluation_dataset(arguments.dataset)
    report = evaluate_memory_dataset(dataset)
    print(report_as_json(report))
    return 0 if report.passed_cases == report.total_cases else 1


if __name__ == "__main__":
    raise SystemExit(main())
