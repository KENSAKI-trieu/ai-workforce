from evaluation.run_baseline import load_dataset, run_baseline


def test_pre_langchain_baseline_is_reproducible() -> None:
    report = run_baseline(load_dataset())
    summary = report["summary"]

    assert report["dataset_version"] == "pre-langchain-v1"
    assert report["runtime"] == "legacy"
    assert summary["case_count"] == 15
    assert summary["routing_accuracy"] == 0.6
    assert summary["grounded_term_recall"] == 1.0
    assert summary["citation_coverage"] == 1.0
    assert summary["chat_contract_accuracy"] == 1.0
    assert summary["estimated_cost_usd"] == 0.0
    assert summary["total_tokens"] > 0

    route_gap = next(item for item in report["known_gaps"] if item["code"] == "MISSING_AGENT_ROUTES")
    assert route_gap["roles"] == ["KNOWLEDGE", "LEGAL"]
