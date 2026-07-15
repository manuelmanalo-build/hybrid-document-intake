# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

from evaluation.harness import EvaluationCase, EvaluationReport, evaluate, load_cases


def main() -> None:
    cases = load_cases(
        Path(os.environ.get("EVALUATION_DATASET", "test-data/evaluation/cases.json"))
    )
    report = evaluate(cases)
    sys.stdout.write(f"{report.model_dump_json(indent=2)}\n")
    if os.environ.get("EVALUATION_LANGSMITH_ENABLED", "false").casefold() == "true":
        export_langsmith(cases, report)


def export_langsmith(cases: list[EvaluationCase], report: EvaluationReport) -> None:
    if not os.environ.get("LANGSMITH_API_KEY"):
        raise RuntimeError("LANGSMITH_API_KEY is required for optional evaluation export")
    module = importlib.import_module("langsmith")
    client = module.Client()
    dataset_name = os.environ.get(
        "EVALUATION_LANGSMITH_DATASET", "document-intake-local-evaluation"
    )
    dataset = client.create_dataset(
        dataset_name=dataset_name,
        description="Sanitized deterministic document-intake evaluation fixtures.",
    )
    for case in cases:
        client.create_example(
            inputs={"case_id": case.case_id, "fixture": case.fixture},
            outputs=case.observation.model_dump(mode="json"),
            dataset_id=dataset.id,
        )
    output = Path(os.environ.get("EVALUATION_REPORT", "evaluation-report.json"))
    output.write_text(json.dumps(report.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()
