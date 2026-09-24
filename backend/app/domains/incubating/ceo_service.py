"""
CEO Master Orchestrator Service for AI Workforce Platform -- UNDER DEVELOPMENT.

Decomposes an executive prompt into a DAG of subtasks for other agents. Nothing is
delegated yet: every node is a fixed onboarding template, so each one is reported as
PLANNED. It used to report them COMPLETED ("email and VPN issued", "added to payroll")
while nothing had happened. Delegation belongs in the CEO graph, calling the other
agents' tools, once those exist.
"""

import logging
import re
import uuid
from typing import Dict, Any, List
from sqlalchemy.orm import Session

from app.models.models import User, AgentWorkflow

logger = logging.getLogger(__name__)


def generate_and_execute_ceo_dag(db: Session, user: User, prompt: str) -> Dict[str, Any]:
    """
    Analyzes user prompt, builds a DAG plan, executes subtasks, and synthesizes final summary.
    Example prompt: "Onboard nhân viên mới Nguyễn Văn A vào vị trí IT Support"
    """
    prompt_lower = prompt.lower()
    
    # Extract employee name if present
    name_match = re.search(r'nhân viên (mới\s+)?([A-ZÀ-Ỹa-zà-ỹ\s]+)', prompt)
    emp_name = name_match.group(2).strip() if name_match else "Nguyễn Văn A"

    # Create DAG nodes for Onboarding / Executive workflow
    workflow_id = uuid.uuid4()
    dag_nodes = [
        {
            "node_id": "task_hr_profile",
            "assigned_agent": "HR",
            "agent_emoji": "🧑‍💼",
            "title": f"Tạo hồ sơ nhân viên {emp_name} & Cấp ngày phép năm",
            "status": "PLANNED",
            "result": f"Đề xuất: tạo hồ sơ nhân viên {emp_name} và cấp quỹ phép năm.",
        },
        {
            "node_id": "task_it_credentials",
            "assigned_agent": "IT",
            "agent_emoji": "💻",
            "title": f"Cấp tài khoản Email công ty & VPN cho {emp_name}",
            "status": "PLANNED",
            "result": "Đề xuất: cấp email công ty và quyền VPN.",
        },
        {
            "node_id": "task_finance_payroll",
            "assigned_agent": "FINANCE",
            "agent_emoji": "💰",
            "title": f"Thêm {emp_name} vào danh sách tính lương phòng IT",
            "status": "PLANNED",
            "result": "Đề xuất: thêm nhân viên vào danh sách tính lương.",
        },
        {
            "node_id": "task_knowledge_handbook",
            "assigned_agent": "KNOWLEDGE",
            "agent_emoji": "📚",
            "title": "Gửi Sổ tay nhân viên & Quy định văn hóa doanh nghiệp",
            "status": "PLANNED",
            "result": "Đề xuất: gửi Sổ tay nhân viên.",
        },
    ]

    # Save DAG workflow session to database
    workflow = AgentWorkflow(
        id=workflow_id,
        tenant_id=user.tenant_id,
        initiator_id=user.id,
        title=f"CEO Plan: {prompt[:50]}",
        status="PENDING",
        dag_plan={"nodes": dag_nodes, "prompt": prompt},
    )
    db.add(workflow)
    db.commit()

    summary_reply = (
        f"Tôi đã lập **kế hoạch đề xuất** gồm {len(dag_nodes)} bước cho chỉ thị *\"{prompt}\"*. "
        "Chưa bước nào được thực thi: việc giao cho HR, IT, Finance và Knowledge "
        "sẽ có khi CEO agent hoàn thiện."
    )

    return {
        "reply": summary_reply,
        "dag_plan_card": {
            "workflow_id": str(workflow_id),
            "title": f"DAG Execution Graph: {prompt[:40]}",
            "nodes": dag_nodes,
            "overall_status": "PLANNED",
        },
    }
