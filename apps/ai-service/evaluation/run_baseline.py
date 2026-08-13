from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import time
from pathlib import Path
from typing import Any

from app.agents.base.registry import agent_registry
from app.llm.local_provider import LocalProvider
from app.rag.generation.answer_generator import grounded_excerpt_answer


ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET = ROOT / "datasets" / "baseline_v1.json"


def load_dataset(path: Path = DEFAULT_DATASET) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 4)


def _average(values: list[float]) -> float:
    return round(statistics.fmean(values), 4) if values else 0.0


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return round(ordered[index], 4)


def run_baseline(dataset: dict[str, Any]) -> dict[str, Any]:
    case_results: list[dict[str, Any]] = []
    latencies: list[float] = []
    routing_hits = 0
    grounded_recalls: list[float] = []
    citation_hits = 0
    chat_hits = 0
    prompt_tokens = 0
    completion_tokens = 0

    for case in dataset["routing_cases"]:
        started = time.perf_counter()
        resolved = agent_registry.resolve(None, case["message"])
        latency = _elapsed_ms(started)
        passed = resolved.role == case["expected_role"]
        routing_hits += int(passed)
        latencies.append(latency)
        case_results.append({
            "id": case["id"],
            "kind": "routing",
            "agent": case["agent"],
            "passed": passed,
            "expected_role": case["expected_role"],
            "actual_role": resolved.role,
            "latency_ms": latency,
        })

    for case in dataset["grounded_cases"]:
        started = time.perf_counter()
        answer = grounded_excerpt_answer(case["chunks"])
        latency = _elapsed_ms(started)
        normalized_answer = answer.casefold()
        expected_terms = case["expected_terms"]
        matched_terms = [term for term in expected_terms if term.casefold() in normalized_answer]
        term_recall = len(matched_terms) / max(len(expected_terms), 1)
        source = case["chunks"][0].get("source_file")
        citation_ok = bool(source and source.casefold() in normalized_answer and "[nguồn:" in normalized_answer)
        grounded_recalls.append(term_recall)
        citation_hits += int(citation_ok)
        latencies.append(latency)
        case_results.append({
            "id": case["id"],
            "kind": "grounded_answer",
            "agent": case["agent"],
            "passed": term_recall == 1.0 and citation_ok,
            "term_recall": round(term_recall, 4),
            "citation_present": citation_ok,
            "latency_ms": latency,
        })

    local_provider = LocalProvider()
    for case in dataset["chat_cases"]:
        started = time.perf_counter()
        result = local_provider.generate([{"role": "user", "content": case["message"]}])
        latency = _elapsed_ms(started)
        passed = case["message"] in result.content
        chat_hits += int(passed)
        prompt_tokens += int(result.usage.get("prompt_tokens", 0))
        completion_tokens += int(result.usage.get("completion_tokens", 0))
        latencies.append(latency)
        case_results.append({
            "id": case["id"],
            "kind": "chat_contract",
            "agent": case["agent"],
            "passed": passed,
            "provider": result.provider,
            "model": result.model,
            "usage": result.usage,
            "latency_ms": latency,
        })

    routing_total = len(dataset["routing_cases"])
    grounded_total = len(dataset["grounded_cases"])
    chat_total = len(dataset["chat_cases"])
    routing_accuracy = routing_hits / max(routing_total, 1)
    grounded_term_recall = statistics.fmean(grounded_recalls) if grounded_recalls else 0.0
    citation_coverage = citation_hits / max(grounded_total, 1)
    chat_contract_accuracy = chat_hits / max(chat_total, 1)
    quality_score = statistics.fmean([
        routing_accuracy,
        grounded_term_recall,
        citation_coverage,
        chat_contract_accuracy,
    ])
    missing_routes = sorted({
        item["expected_role"]
        for item in case_results
        if item["kind"] == "routing" and not item["passed"]
    })

    return {
        "dataset_version": dataset["version"],
        "mode": "offline_deterministic",
        "runtime": "legacy",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.system(),
            "llm_provider": "local",
            "external_model_calls": 0,
        },
        "summary": {
            "case_count": len(case_results),
            "quality_score": round(quality_score, 4),
            "routing_accuracy": round(routing_accuracy, 4),
            "grounded_term_recall": round(grounded_term_recall, 4),
            "citation_coverage": round(citation_coverage, 4),
            "chat_contract_accuracy": round(chat_contract_accuracy, 4),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "estimated_cost_usd": 0.0,
            "latency_avg_ms": _average(latencies),
            "latency_p95_ms": _p95(latencies),
        },
        "known_gaps": [
            {
                "code": "MISSING_AGENT_ROUTES",
                "roles": missing_routes,
                "description": "Expected domain roles not registered in the current AI Service router.",
            }
        ] if missing_routes else [],
        "limitations": [
            "Offline local-provider results do not measure production LLM semantic quality.",
            "Cost is zero because the baseline makes no external model calls.",
            "Latency covers in-process AI Service components, not network or database time.",
        ],
        "cases": case_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the pre-LangChain offline baseline")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = run_baseline(load_dataset(args.dataset))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
