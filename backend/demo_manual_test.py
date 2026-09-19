"""
Manual feature test script for AI Workforce platform.
Demonstrates end-to-end user journeys across HR, Knowledge RAG, Specialized Agents, CEO Orchestration, HITL Approvals, and Audit Logging.
"""

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings
import json

def run_manual_demo():
    if not settings.SEED_DEFAULT_PASSWORD:
        raise RuntimeError(
            "Set SEED_DEFAULT_PASSWORD in backend/.env before running the manual demo."
        )
    demo_password = settings.SEED_DEFAULT_PASSWORD
    client = TestClient(app)
    print("=" * 70)
    print("🚀 BẮT ĐẦU TEST THỦ CÔNG CÁC TÍNH NĂNG NỀN TẢNG AI WORKFORCE")
    print("=" * 70)

    # 1. Test Login CEO & Employee
    print("\n1️⃣ TEST AUTHENTICATION & LOGIN")
    ceo_res = client.post("/api/v1/auth/login", json={"email": "admin@company.com", "password": demo_password})
    assert ceo_res.status_code == 200
    ceo_data = ceo_res.json()
    ceo_token = ceo_data["access_token"]
    ceo_headers = {"Authorization": f"Bearer {ceo_token}"}
    print(f"  ✅ Đăng nhập CEO thành công: {ceo_data['user']['full_name']} ({ceo_data['user']['role']} - {ceo_data['user']['department']})")

    emp_res = client.post("/api/v1/auth/login", json={"email": "employee@company.com", "password": demo_password})
    assert emp_res.status_code == 200
    emp_data = emp_res.json()
    emp_token = emp_data["access_token"]
    emp_headers = {"Authorization": f"Bearer {emp_token}"}
    print(f"  ✅ Đăng nhập Nhân viên thành công: {emp_data['user']['full_name']} ({emp_data['user']['role']} - {emp_data['user']['department']})")

    # 2. Test HR Agent & Approval Card
    print("\n2️⃣ TEST HR AGENT & QUY TRÌNH XIN NGHỈ PHÉP")
    hr_chat = client.post(
        "/api/v1/agent/chat",
        json={"agent_role": "HR", "message": "Tôi muốn xin nghỉ phép 2 ngày vào tuần tới vì việc gia đình"},
        headers=emp_headers
    )
    assert hr_chat.status_code == 200
    hr_res = hr_chat.json()
    card = hr_res["approval_card"]
    print(f"  💬 Phản hồi của HR Agent:\n    \"{hr_res['reply'][:120]}...\"")
    print(f"  🛡️ Thẻ Phê Duyệt được tạo: ID={card['id'][:8]}, Loại={card['action_type']}, Trạng thái={card['status']}")

    # Approve request as CEO
    app_res = client.post(
        f"/api/v1/approvals/{card['id']}/action",
        json={"action": "APPROVE", "comments": "CEO phê duyệt cho nghỉ 2 ngày."},
        headers=ceo_headers
    )
    assert app_res.status_code == 200
    print(f"  ✅ CEO bấm Phê duyệt: {app_res.json()['message']}")

    # 3. Test Knowledge Agent & Hybrid RAG Citations
    print("\n3️⃣ TEST KNOWLEDGE AGENT & TRUY XUẤT RAG TRÍCH DẪN NGUỒN")
    rag_chat = client.post(
        "/api/v1/agent/chat",
        json={"agent_role": "KNOWLEDGE", "message": "Quy định về hạn mức công tác phí khách sạn và đi lại là bao nhiêu?"},
        headers=emp_headers
    )
    assert rag_chat.status_code == 200
    rag_res = rag_chat.json()
    print(f"  💬 Phản hồi RAG:\n    \"{rag_res['reply'][:160]}...\"")
    print(f"  📌 Trích dẫn nguồn: {[c['citation_tag'] for c in rag_res['citations']]}")

    # 4. Test Legal Agent Contract Audit
    print("\n4️⃣ TEST LEGAL AGENT (THẨM ĐỊNH HỢP ĐỒNG & REDLINE)")
    legal_contract = (
        "HỢP ĐỒNG DỊCH VỤ\n"
        "Điều 1. Phạt vi phạm\nMức phạt 30% giá trị hợp đồng.\n"
        "Điều 2. Chấm dứt\nBên A đơn phương chấm dứt ngay lập tức.\n"
        "Điều 3. Trách nhiệm\nBên B chịu trách nhiệm không giới hạn."
    )
    legal_ask = client.post(
        "/api/v1/agent/chat",
        json={"agent_role": "LEGAL", "message": legal_contract},
        headers=emp_headers
    )
    assert legal_ask.status_code == 200
    print(f"  ❓ Legal hỏi góc nhìn trước khi rà soát: {legal_ask.json()['legal_risk_card']['status']}")

    legal_chat = client.post(
        "/api/v1/agent/chat",
        json={
            "agent_role": "LEGAL",
            "message": "Tôi là bên A",
            "conversation_id": legal_ask.json()["conversation_id"],
        },
        headers=emp_headers
    )
    assert legal_chat.status_code == 200
    legal_res = legal_chat.json()
    lcard = legal_res["legal_risk_card"]
    print(f"  ⚖️ Kết quả rà soát Legal ({lcard['represented_party']}): Phát hiện {lcard['total_risks_found']} điều khoản rủi ro.")
    print(f"  📄 Redline mở sau khi có đề xuất được chấp nhận: {lcard.get('redline_url')}")

    # 5. Test IT Agent Jira Ticket
    print("\n5️⃣ TEST IT AGENT (SỰ CỐ KỸ THUẬT & TỰ ĐỘNG TẠO JIRA TICKET)")
    it_chat = client.post(
        "/api/v1/agent/chat",
        json={"agent_role": "IT", "message": "Sự cố mạng VPN bị đứt kết nối không truy cập được server nội bộ"},
        headers=emp_headers
    )
    assert it_chat.status_code == 200
    it_res = it_chat.json()
    jcard = it_res["jira_card"]
    print(f"  💻 Thẻ Jira Ticket được khởi tạo: Key={jcard['ticket_key']}, Ưu tiên={jcard['priority']}, Trạng thái={jcard['status']}")

    # 6. Test Finance Agent Invoice Audit
    print("\n6️⃣ TEST FINANCE AGENT (ĐỐI SOÁT HÓA ĐƠN & PO DATABASE)")
    fin_chat = client.post(
        "/api/v1/agent/chat",
        json={"agent_role": "FINANCE", "message": "Hóa đơn PO-2025-098 số tiền 15.000.000 VNĐ Công ty Thiết Bị Số"},
        headers=emp_headers
    )
    assert fin_chat.status_code == 200
    fin_res = fin_chat.json()
    icard = fin_res["invoice_card"]
    print(f"  💰 Kết quả đối soát Hóa đơn PO: PO={icard['po_number']}, Trạng thái={icard['status']}")
    print(f"  ⚠️ Bất thường tài chính: {icard['anomalies']}")

    # 7. Test Sales Agent Quote
    print("\n7️⃣ TEST SALES AGENT (TRA CỨU TỒN KHO & SINH BÁO GIÁ PDF)")
    sales_chat = client.post(
        "/api/v1/agent/chat",
        json={"agent_role": "SALES", "message": "Tôi muốn xin báo giá 15 camera AI IP Security"},
        headers=emp_headers
    )
    assert sales_chat.status_code == 200
    sales_res = sales_chat.json()
    qcard = sales_res["quote_card"]
    print(f"  📈 Bảng báo giá: Mã={qcard['id']}, Khách={qcard['customer_name']}, Tổng tiền={qcard['total_amount']}")

    # 8. Test CEO Master Agent DAG Orchestration
    print("\n8️⃣ TEST CEO MASTER AGENT (LẬP KẾ HOẠCH ĐA AGENT DAG)")
    ceo_chat = client.post(
        "/api/v1/agent/chat",
        json={"agent_role": "CEO", "message": "Onboard nhân viên mới Nguyễn Văn A vào vị trí IT Support"},
        headers=ceo_headers
    )
    assert ceo_chat.status_code == 200
    ceo_res_data = ceo_chat.json()
    dcard = ceo_res_data["dag_plan_card"]
    print(f"  👔 Đồ thị thực thi DAG CEO: {dcard['title']}")
    for node in dcard["nodes"]:
        print(f"    └─ Node {node['node_id']} [{node['assigned_agent']} Agent]: {node['title']} -> {node['status']}")

    # 9. Test Audit Logs & LLM Cost Metering
    print("\n9️⃣ TEST AUDIT LOGS & BÁO CÁO CHI PHÍ LLM")
    audit_res = client.get("/api/v1/audit/logs", headers=ceo_headers)
    assert audit_res.status_code == 200
    logs = audit_res.json()
    print(f"  📋 Nhật ký Audit Trail: Đã ghi nhận {len(logs)} thao tác công cụ của Agents.")

    cost_res = client.get("/api/v1/audit/costs", headers=ceo_headers)
    assert cost_res.status_code == 200
    costs = cost_res.json()
    print(f"  💵 Báo cáo chi phí LLM: Tổng Yêu Cầu={costs['total_requests']}, Tổng Tokens={costs['total_tokens']}, Chi phí ước tính=${costs['total_estimated_cost_usd']}")

    print("\n" + "=" * 70)
    print("✅ HOÀN THÀNH TẤT CẢ CÁC BÀI TEST THỦ CÔNG — TẤT CẢ TÍNH NĂNG HOẠT ĐỘNG HOÀN HẢO!")
    print("=" * 70)

if __name__ == "__main__":
    run_manual_demo()
