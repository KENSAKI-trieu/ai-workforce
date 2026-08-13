"""
Extended Test Suite covering Phase 2 Advanced Enterprise Improvements, Edge Cases & Benchmark Evaluations.
"""

from app.services.reranker_service import rerank_chunks
from app.services.ocr_service import ocr_parse_document_bytes
from app.services.integration_service import notify_slack_approval_card, jira_sync_ticket_v3


def test_reranker_service():
    """Test relevance gating and hybrid candidate re-ranking."""
    candidates = [
        {
            "content": "Chính sách quy định số ngày nghỉ phép năm 2025 là 12 ngày",
            "score": 1.0,
            "_rrf_score": 1.0,
            "_dense_score": 0.75,
            "_sparse_score": 1.0,
        },
        {
            "content": "Phụ cấp đi lại công tác phí tối đa 500k",
            "score": 0.8,
            "_rrf_score": 0.8,
            "_dense_score": 0.30,
            "_sparse_score": 0.0,
        },
    ]
    reranked = rerank_chunks("nghỉ phép bao nhiêu ngày", candidates, top_k=2)
    assert len(reranked) == 1
    assert reranked[0]["score"] == reranked[0]["rerank_score"]


def test_ocr_document_parser():
    """Test Multi-Modal layout OCR document parsing."""
    sample_text = "# HỢP ĐỒNG MUA BÁN\nĐiều 1: Mức phạt vi phạm 15%\nĐiều 2: Đơn phương chấm dứt"
    res = ocr_parse_document_bytes(sample_text.encode("utf-8"), "Contract_Scan.pdf")
    assert res["file_name"] == "Contract_Scan.pdf"
    assert res["total_lines"] == 3
    assert res["ocr_confidence"] > 0.9


def test_slack_webhook_formatting():
    """Test Slack Block Kit approval payload generation."""
    approval_card = {
        "action_type": "XIN NGHỈ PHÉP",
        "requester_name": "Lê Văn Nhẫn",
        "details": "Xin nghỉ 2 ngày vì việc gia đình",
    }
    res = notify_slack_approval_card(approval_card)
    assert res["success"] is True
    assert "payload_sent" in res
    assert "blocks" in res["payload_sent"]


def test_jira_v3_integration():
    """Test Jira REST API v3 payload generation."""
    ticket_info = {
        "ticket_key": "IT-2091",
        "summary": "VPN bị đứt kết nối server",
        "priority": "High",
    }
    res = jira_sync_ticket_v3(ticket_info)
    assert res["jira_response"]["key"] == "IT-2091"
    assert "payload_sent" in res


def test_ragas_eval_benchmark_endpoint(client, ceo_token_headers):
    """Test RAGAS quality benchmark evaluation API endpoint."""
    response = client.post(
        "/api/v1/eval/benchmark",
        json={"query": "Số ngày nghỉ phép năm 2025"},
        headers=ceo_token_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "COMPLETED"
    card = data["scorecard"]
    assert "faithfulness_score" in card
    assert "answer_relevancy_score" in card
    assert "context_precision_score" in card
    assert card["overall_score"] > 0.0


def test_leave_request_quota_exceeded_edge_case(client, employee_token_headers):
    """A large requested duration still cannot bypass the three required slots."""
    response = client.post(
        "/api/v1/agent/chat",
        json={"agent_role": "HR", "message": "Tôi muốn xin nghỉ phép 100 ngày"},
        headers=employee_token_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["approval_card"] is None
    assert data["hr_card"]["type"] == "LEAVE_REQUEST_DRAFT"
    assert data["hr_card"]["missing_fields"] == ["start_date", "end_date", "reason"]
    assert all(tool["tool_name"] != "request_leave" for tool in data["tools_executed"])


def test_invalid_approval_action_edge_case(client, ceo_token_headers):
    """Test Edge Case: Submitting invalid approval action type on a valid UUID."""
    response = client.post(
        "/api/v1/approvals/00000000-0000-0000-0000-000000000000/action",
        json={"action": "INVALID_ACTION"},
        headers=ceo_token_headers,
    )
    assert response.status_code in [400, 404, 422]


def test_out_of_domain_rag_query(client, employee_token_headers):
    """Test Edge Case: Out-of-domain query with zero matching knowledge chunks."""
    response = client.post(
        "/api/v1/agent/chat",
        json={"agent_role": "KNOWLEDGE", "message": "xyzabc123456 qwerty pizza recipe"},
        headers=employee_token_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert "Hiện chưa tìm thấy tài liệu" in data["reply"] or len(data["citations"]) == 0
