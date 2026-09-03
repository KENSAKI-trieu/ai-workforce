"""Structured HR operations used by both REST endpoints and the HR AI employee."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import (
    AgentWorkflow,
    EmploymentContract,
    HRCalendarEvent,
    LeaveBalance,
    LeaveLedger,
    LeaveRequest,
    OnboardingCase,
    OnboardingStep,
    Task,
    User,
    UserMemory,
    WorkflowApproval,
)
from app.services.audit_events import add_audit_event
from app.services.notification_service import create_notification
from app.services.position_service import has_permission

LEAVE_TYPES_WITH_BALANCE = {"ANNUAL", "SICK"}
ACTIVE_LEAVE_STATUSES = {"WAITING", "APPROVED"}


def is_hr_user(user: User) -> bool:
    """Whether this person runs HR operations.

    Used to be "sits in the department literally coded HR and is a Manager or Admin",
    which meant a company that coded its HR department differently had no HR back office
    at all. It is now a permission the company grants to whichever position it likes.
    """
    return has_permission(None, user, "hr.employee.manage")


def can_manage_hr(user: User) -> bool:
    return has_company_hr_scope(user) or is_hr_user(user)


def has_company_hr_scope(user: User) -> bool:
    """Whether this person reads the whole tenant directory rather than their own branch."""
    return has_permission(None, user, "hr.scope.company")


def has_reporting_hr_scope(user: User) -> bool:
    """Whether this person reads the branch of the org tree below them."""
    return has_permission(None, user, "hr.scope.reports")


def authorized_employee_ids(db: Session, current_user: User) -> set[uuid.UUID]:
    """Resolve self/company/reporting-tree scope without trusting UI-provided IDs."""
    if has_company_hr_scope(current_user):
        return {
            item[0] for item in db.query(User.id).filter(
                User.tenant_id == current_user.tenant_id
            ).all()
        }
    allowed = {current_user.id}
    if not has_reporting_hr_scope(current_user):
        return allowed

    employees = db.query(User.id, User.manager_id).filter(
        User.tenant_id == current_user.tenant_id,
        User.is_active.is_(True),
    ).all()
    frontier = {current_user.id}
    while frontier:
        next_frontier = {
            employee_id
            for employee_id, manager_id in employees
            if manager_id in frontier
            and employee_id not in allowed
        }
        allowed.update(next_frontier)
        frontier = next_frontier
    return allowed


def hr_scope_label(user: User) -> str:
    if has_company_hr_scope(user):
        return "COMPANY"
    if has_reporting_hr_scope(user):
        return "REPORTING_TREE"
    return "SELF"


def can_view_employee(db: Session, current_user: User, target: User) -> bool:
    if current_user.tenant_id != target.tenant_id:
        return False
    if current_user.id == target.id or has_company_hr_scope(current_user):
        return True
    if not has_reporting_hr_scope(current_user):
        return False
    return target.id in authorized_employee_ids(db, current_user)


def scoped_employee_query(db: Session, current_user: User):
    query = db.query(User).filter(User.tenant_id == current_user.tenant_id)
    if has_company_hr_scope(current_user):
        return query
    return query.filter(User.id.in_(authorized_employee_ids(db, current_user)))


def can_approve_hr_request(db: Session, current_user: User, approval: WorkflowApproval) -> bool:
    """Apply the same reporting-tree scope to HR approval cards."""
    if approval.approver_id == current_user.id or has_company_hr_scope(current_user):
        return True
    requester_id = (approval.payload or {}).get("requester_id")
    if not requester_id or not has_reporting_hr_scope(current_user):
        return False
    try:
        requester_uuid = uuid.UUID(str(requester_id))
    except (TypeError, ValueError):
        return False
    return requester_uuid in authorized_employee_ids(db, current_user)


def _legacy_leave_balance(db: Session, user: User) -> tuple[Decimal, Decimal]:
    memory = db.query(UserMemory).filter(
        UserMemory.tenant_id == user.tenant_id,
        UserMemory.user_id == user.id,
        UserMemory.memory_key == "leave_balance",
    ).first()
    if memory and memory.memory_value:
        try:
            value = json.loads(memory.memory_value)
            return (
                Decimal(str(value.get("total_days", 12))),
                Decimal(str(value.get("used_days", 0))),
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return Decimal("12.00"), Decimal("0.00")


def get_or_create_leave_balance(
    db: Session,
    user: User,
    year: int | None = None,
    *,
    lock: bool = False,
) -> LeaveBalance:
    balance_year = year or date.today().year
    query = db.query(LeaveBalance).filter(
        LeaveBalance.tenant_id == user.tenant_id,
        LeaveBalance.user_id == user.id,
        LeaveBalance.year == balance_year,
    )
    if lock:
        query = query.with_for_update()
    balance = query.first()
    if balance:
        return balance

    allocated, used = _legacy_leave_balance(db, user)
    balance = LeaveBalance(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        year=balance_year,
        allocated_days=allocated,
        carried_over_days=Decimal("0.00"),
        used_days=used,
        reserved_days=Decimal("0.00"),
    )
    db.add(balance)
    db.flush()
    return balance


def serialize_leave_balance(balance: LeaveBalance) -> dict[str, Any]:
    total = Decimal(balance.allocated_days) + Decimal(balance.carried_over_days)
    used = Decimal(balance.used_days)
    reserved = Decimal(balance.reserved_days)
    return {
        "year": balance.year,
        "total_days": float(total),
        "used_days": float(used),
        "reserved_days": float(reserved),
        "remaining_days": float(max(Decimal("0.00"), total - used - reserved)),
    }


def query_leave_balance(db: Session, user: User, year: int | None = None) -> dict[str, Any]:
    balance = get_or_create_leave_balance(db, user, year)
    return {
        "user_id": str(user.id),
        "user_name": user.full_name,
        **serialize_leave_balance(balance),
    }


def calculate_business_days(start_date: date, end_date: date, part_of_day: str) -> Decimal:
    if end_date < start_date:
        raise HTTPException(status_code=422, detail="End date must be on or after start date")
    if part_of_day != "FULL_DAY" and start_date != end_date:
        raise HTTPException(status_code=422, detail="Half-day leave must start and end on the same date")
    weekdays = sum(
        1
        for offset in range((end_date - start_date).days + 1)
        if (start_date + timedelta(days=offset)).weekday() < 5
    )
    if weekdays == 0:
        raise HTTPException(status_code=422, detail="The selected period contains no working day")
    return Decimal("0.50") if part_of_day != "FULL_DAY" else Decimal(weekdays)


def _find_approver(db: Session, user: User) -> User | None:
    if user.manager_id:
        manager = db.query(User).filter(
            User.id == user.manager_id,
            User.tenant_id == user.tenant_id,
            User.is_active.is_(True),
        ).first()
        if manager:
            return manager
    department_manager = db.query(User).filter(
        User.tenant_id == user.tenant_id,
        User.department == user.department,
        User.role == "Manager",
        User.is_active.is_(True),
        User.id != user.id,
    ).first()
    if department_manager:
        return department_manager
    return db.query(User).filter(
        User.tenant_id == user.tenant_id,
        User.role.in_(["Owner", "CEO"]),
        User.is_active.is_(True),
    ).first()


def _sync_legacy_balance(db: Session, user: User, balance: LeaveBalance) -> None:
    serialized = serialize_leave_balance(balance)
    memory = db.query(UserMemory).filter(
        UserMemory.tenant_id == user.tenant_id,
        UserMemory.user_id == user.id,
        UserMemory.memory_key == "leave_balance",
    ).first()
    if not memory:
        memory = UserMemory(
            id=uuid.uuid4(),
            tenant_id=user.tenant_id,
            user_id=user.id,
            memory_category="hr",
            memory_key="leave_balance",
            memory_value="{}",
            confidence_score=1.0,
        )
        db.add(memory)
    memory.memory_value = json.dumps(
        {
            "total_days": serialized["total_days"],
            "used_days": serialized["used_days"],
            "remaining_days": serialized["remaining_days"],
        }
    )


def create_leave_request(
    db: Session,
    user: User,
    *,
    start_date: date,
    end_date: date,
    leave_type: str,
    part_of_day: str,
    reason: str,
) -> LeaveRequest:
    if start_date < date.today():
        raise HTTPException(status_code=422, detail="Leave cannot start in the past")
    requested_days = calculate_business_days(start_date, end_date, part_of_day)
    overlap = db.query(LeaveRequest).filter(
        LeaveRequest.tenant_id == user.tenant_id,
        LeaveRequest.employee_id == user.id,
        LeaveRequest.status.in_(ACTIVE_LEAVE_STATUSES),
        LeaveRequest.start_date <= end_date,
        LeaveRequest.end_date >= start_date,
    ).first()
    if overlap:
        raise HTTPException(status_code=409, detail="This leave period overlaps an existing request")

    approver = _find_approver(db, user)
    if not approver:
        raise HTTPException(status_code=409, detail="No eligible manager is configured for this employee")

    balance = get_or_create_leave_balance(db, user, start_date.year, lock=True)
    available = (
        Decimal(balance.allocated_days)
        + Decimal(balance.carried_over_days)
        - Decimal(balance.used_days)
        - Decimal(balance.reserved_days)
    )
    if leave_type in LEAVE_TYPES_WITH_BALANCE and requested_days > available:
        raise HTTPException(
            status_code=409,
            detail=f"Insufficient leave balance: requested {requested_days} days, available {available} days",
        )

    workflow = AgentWorkflow(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        initiator_id=user.id,
        title=f"Đơn nghỉ phép {requested_days:g} ngày — {user.full_name}",
        status="AWAITING_APPROVAL",
        current_step=1,
        dag_plan={
            "type": "LEAVE_REQUEST",
            "employee_id": str(user.id),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "requested_days": float(requested_days),
        },
    )
    db.add(workflow)
    db.flush()
    approval = WorkflowApproval(
        id=uuid.uuid4(),
        workflow_id=workflow.id,
        approver_id=approver.id,
        action_type="LEAVE_REQUEST",
        risk_level="LOW",
        payload={},
        status="WAITING",
        expires_at=datetime.now(timezone.utc) + timedelta(days=2),
    )
    db.add(approval)
    db.flush()
    leave_request = LeaveRequest(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        employee_id=user.id,
        manager_id=approver.id,
        workflow_id=workflow.id,
        approval_id=approval.id,
        leave_type=leave_type,
        start_date=start_date,
        end_date=end_date,
        part_of_day=part_of_day,
        requested_days=requested_days,
        reason=reason.strip(),
        status="WAITING",
    )
    db.add(leave_request)
    db.flush()
    approval.payload = {
        "leave_request_id": str(leave_request.id),
        "requester_id": str(user.id),
        "requester_name": user.full_name,
        "requester_email": user.email,
        "leave_type": leave_type,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "part_of_day": part_of_day,
        "days_requested": float(requested_days),
        "remaining_days": float(available),
        "reason": reason.strip(),
        "data_sources": ["leave_balances", "leave_requests"],
    }
    if leave_type in LEAVE_TYPES_WITH_BALANCE:
        balance.reserved_days = Decimal(balance.reserved_days) + requested_days
        db.add(LeaveLedger(
            id=uuid.uuid4(),
            tenant_id=user.tenant_id,
            balance_id=balance.id,
            leave_request_id=leave_request.id,
            entry_type="RESERVATION",
            amount_days=requested_days,
            balance_after=available - requested_days,
            note="Reserved while awaiting manager approval",
            actor_user_id=user.id,
        ))
        _sync_legacy_balance(db, user, balance)

    create_notification(
        db,
        user=approver,
        event_type="LEAVE_APPROVAL_REQUIRED",
        title="Đơn nghỉ phép cần phê duyệt",
        message=f"{user.full_name} xin nghỉ {requested_days:g} ngày từ {start_date:%d/%m/%Y}",
        severity="WARNING",
        entity_type="LEAVE_REQUEST",
        entity_id=str(leave_request.id),
        dedup_key=f"leave-approval:{leave_request.id}",
    )
    add_audit_event(
        db,
        tenant_id=user.tenant_id,
        actor_user=user,
        agent_role="HR",
        action="hr.leave_request.created",
        tool_name="create_leave_request",
        resource_type="LEAVE_REQUEST",
        resource_id=str(leave_request.id),
        workflow_id=workflow.id,
        input_parameters={
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "leave_type": leave_type,
        },
        after_data={"status": "WAITING", "requested_days": float(requested_days)},
    )
    db.commit()
    return leave_request


def request_leave(
    db: Session,
    user: User,
    days: int | None,
    reason: str,
    start_date: str = "Sắp tới",
    end_date: str | None = None,
) -> dict[str, Any]:
    """Backward-compatible chat tool that creates a real structured request."""
    parsed_start = date.today()
    try:
        parsed_start = date.fromisoformat(start_date)
    except (TypeError, ValueError):
        while parsed_start.weekday() >= 5:
            parsed_start += timedelta(days=1)
    if end_date:
        try:
            parsed_end = date.fromisoformat(end_date)
        except (TypeError, ValueError):
            return {
                "success": False,
                "message": "Ngày kết thúc nghỉ không hợp lệ. Vui lòng dùng định dạng YYYY-MM-DD.",
                "remaining_days": query_leave_balance(db, user)["remaining_days"],
            }
    else:
        parsed_end = parsed_start
        remaining = max(0, (days or 1) - 1)
        while remaining:
            parsed_end += timedelta(days=1)
            if parsed_end.weekday() < 5:
                remaining -= 1
    try:
        record = create_leave_request(
            db,
            user,
            start_date=parsed_start,
            end_date=parsed_end,
            leave_type="ANNUAL",
            part_of_day="FULL_DAY",
            reason=reason,
        )
    except HTTPException as exc:
        remaining_days = query_leave_balance(db, user)["remaining_days"]
        message = str(exc.detail)
        if exc.status_code == 409 and "Insufficient leave balance" in message:
            message = (
                "Không thể gửi đơn xin nghỉ phép. "
                f"Bạn chỉ còn {remaining_days:g} ngày phép khả dụng."
            )
        return {
            "success": False,
            "message": message,
            "remaining_days": remaining_days,
        }
    return {
        "success": True,
        "message": "Đơn nghỉ phép đã được chuyển tới quản lý để phê duyệt.",
        "workflow_id": str(record.workflow_id),
        "approval_id": str(record.approval_id),
        "leave_request_id": str(record.id),
        "approval_card": {
            "id": str(record.approval_id),
            "action_type": "XIN NGHỈ PHÉP",
            "requester_name": user.full_name,
            "details": (
                f"Xin nghỉ {float(record.requested_days):g} ngày từ "
                f"{record.start_date:%d/%m/%Y} đến {record.end_date:%d/%m/%Y}. "
                f"Lý do: {record.reason}."
            ),
            "status": "WAITING",
        },
    }


def finalize_leave_approval(
    db: Session,
    approval: WorkflowApproval,
    approver: User,
    *,
    approved: bool,
    comment: str | None,
) -> LeaveRequest | None:
    request_record = db.query(LeaveRequest).filter(
        LeaveRequest.tenant_id == approver.tenant_id,
        LeaveRequest.approval_id == approval.id,
    ).with_for_update().first()
    if not request_record:
        return None
    if request_record.status != "WAITING":
        raise HTTPException(status_code=409, detail="Leave request has already been decided")

    employee = request_record.employee
    balance = get_or_create_leave_balance(
        db, employee, request_record.start_date.year, lock=True
    )
    requested_days = Decimal(request_record.requested_days)
    if request_record.leave_type in LEAVE_TYPES_WITH_BALANCE:
        balance.reserved_days = max(
            Decimal("0.00"), Decimal(balance.reserved_days) - requested_days
        )
        if approved:
            balance.used_days = Decimal(balance.used_days) + requested_days
        available_after = (
            Decimal(balance.allocated_days)
            + Decimal(balance.carried_over_days)
            - Decimal(balance.used_days)
            - Decimal(balance.reserved_days)
        )
        db.add(LeaveLedger(
            id=uuid.uuid4(),
            tenant_id=employee.tenant_id,
            balance_id=balance.id,
            leave_request_id=request_record.id,
            entry_type="USAGE" if approved else "RELEASE",
            amount_days=requested_days,
            balance_after=available_after,
            note=comment or ("Approved by manager" if approved else "Rejected by manager"),
            actor_user_id=approver.id,
        ))
        _sync_legacy_balance(db, employee, balance)

    request_record.status = "APPROVED" if approved else "REJECTED"
    request_record.decision_comment = comment
    request_record.decided_by_id = approver.id
    request_record.decided_at = datetime.now(timezone.utc)
    if approved:
        start_at = datetime.combine(request_record.start_date, time.min, tzinfo=timezone.utc)
        end_at = datetime.combine(
            request_record.end_date + timedelta(days=1), time.min, tzinfo=timezone.utc
        )
        db.add(HRCalendarEvent(
            id=uuid.uuid4(),
            tenant_id=employee.tenant_id,
            user_id=employee.id,
            event_type="LEAVE",
            title=f"Nghỉ phép — {employee.full_name}",
            start_at=start_at,
            end_at=end_at,
            all_day=request_record.part_of_day == "FULL_DAY",
            source_type="LEAVE_REQUEST",
            source_id=str(request_record.id),
            sync_status="INTERNAL",
        ))
    return request_record


def serialize_leave_request(item: LeaveRequest) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "employee": {
            "id": str(item.employee.id),
            "name": item.employee.full_name,
            "department": item.employee.department,
        },
        "manager_id": str(item.manager_id) if item.manager_id else None,
        "approval_id": str(item.approval_id) if item.approval_id else None,
        "leave_type": item.leave_type,
        "start_date": item.start_date.isoformat(),
        "end_date": item.end_date.isoformat(),
        "part_of_day": item.part_of_day,
        "requested_days": float(item.requested_days),
        "reason": item.reason,
        "status": item.status,
        "decision_comment": item.decision_comment,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def serialize_contract(contract: EmploymentContract) -> dict[str, Any]:
    return {
        "id": str(contract.id),
        "employee": {"id": str(contract.employee.id), "name": contract.employee.full_name},
        "contract_number": contract.contract_number,
        "contract_type": contract.contract_type,
        "status": contract.status,
        "start_date": contract.start_date.isoformat(),
        "end_date": contract.end_date.isoformat() if contract.end_date else None,
        "probation_end_date": contract.probation_end_date.isoformat() if contract.probation_end_date else None,
        "signed_by_employee": contract.signed_by_employee,
        "signed_by_company": contract.signed_by_company,
        "document_name": contract.document_name,
        "document_url": contract.document_url,
        "days_until_expiry": (contract.end_date - date.today()).days if contract.end_date else None,
    }


ONBOARDING_TEMPLATE = (
    ("hr_documents", "Hoàn thiện hồ sơ và hợp đồng", "HR", 1),
    ("it_accounts", "Tạo email và tài khoản hệ thống", "IT", 0),
    ("it_equipment", "Chuẩn bị và bàn giao thiết bị", "IT", 0),
    ("manager_plan", "Giao kế hoạch thử việc", "MANAGER", 1),
    ("finance_payroll", "Thêm thông tin trả lương", "FINANCE", 2),
    ("employee_policy", "Đọc và xác nhận nội quy", "EMPLOYEE", 3),
    ("feedback_7d", "Thu thập phản hồi sau 7 ngày", "HR", 7),
    ("feedback_30d", "Thu thập phản hồi sau 30 ngày", "HR", 30),
)


def create_onboarding_case(
    db: Session,
    *,
    employee: User,
    creator: User,
    start_date: date,
    probation_end_date: date | None,
    mentor_id: uuid.UUID | None,
) -> OnboardingCase:
    existing = db.query(OnboardingCase).filter(
        OnboardingCase.tenant_id == creator.tenant_id,
        OnboardingCase.employee_id == employee.id,
        OnboardingCase.status == "IN_PROGRESS",
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Employee already has an active onboarding")
    workflow = AgentWorkflow(
        id=uuid.uuid4(),
        tenant_id=creator.tenant_id,
        initiator_id=creator.id,
        title=f"Onboarding — {employee.full_name}",
        status="IN_PROGRESS",
        current_step=0,
        dag_plan={"type": "HR_ONBOARDING", "template": "DEFAULT_V1"},
    )
    db.add(workflow)
    db.flush()
    case = OnboardingCase(
        id=uuid.uuid4(),
        tenant_id=creator.tenant_id,
        employee_id=employee.id,
        workflow_id=workflow.id,
        mentor_id=mentor_id,
        start_date=start_date,
        probation_end_date=probation_end_date,
        status="IN_PROGRESS",
        created_by_id=creator.id,
    )
    db.add(case)
    db.flush()
    for step_key, title, owner_department, day_offset in ONBOARDING_TEMPLATE:
        if owner_department == "EMPLOYEE":
            assignee = employee
        elif owner_department == "MANAGER":
            assignee = employee.manager
        else:
            assignee = db.query(User).filter(
                User.tenant_id == creator.tenant_id,
                User.department == owner_department,
                User.is_active.is_(True),
            ).order_by(User.role.desc()).first()
        due_at = datetime.combine(
            start_date + timedelta(days=day_offset), time(hour=17), tzinfo=timezone.utc
        )
        task = Task(
            id=uuid.uuid4(),
            tenant_id=creator.tenant_id,
            title=f"[Onboarding] {title} — {employee.full_name}",
            description=f"Bước {step_key} của workflow onboarding {case.id}",
            creator_id=creator.id,
            assignee_id=assignee.id if assignee else None,
            priority="HIGH" if day_offset <= 1 else "MEDIUM",
            due_date=due_at,
            status="PENDING",
            attachments=[],
        )
        db.add(task)
        db.flush()
        db.add(OnboardingStep(
            id=uuid.uuid4(),
            onboarding_id=case.id,
            step_key=step_key,
            title=title,
            owner_department=owner_department,
            assignee_id=assignee.id if assignee else None,
            task_id=task.id,
            due_date=due_at,
            status="PENDING",
        ))
        if assignee:
            create_notification(
                db,
                user=assignee,
                event_type="ONBOARDING_TASK_CREATED",
                title="Nhiệm vụ onboarding mới",
                message=task.title,
                entity_type="TASK",
                entity_id=str(task.id),
                dedup_key=f"onboarding-task:{task.id}",
            )
    add_audit_event(
        db,
        tenant_id=creator.tenant_id,
        actor_user=creator,
        agent_role="HR",
        action="hr.onboarding.created",
        tool_name="create_onboarding_workflow",
        resource_type="ONBOARDING",
        resource_id=str(case.id),
        workflow_id=workflow.id,
        after_data={"employee_id": str(employee.id), "steps": len(ONBOARDING_TEMPLATE)},
    )
    db.commit()
    return case


def serialize_onboarding(case: OnboardingCase) -> dict[str, Any]:
    completed = sum(1 for step in case.steps if step.status == "COMPLETED")
    return {
        "id": str(case.id),
        "employee": {"id": str(case.employee.id), "name": case.employee.full_name},
        "start_date": case.start_date.isoformat(),
        "probation_end_date": case.probation_end_date.isoformat() if case.probation_end_date else None,
        "status": case.status,
        "progress": {"completed": completed, "total": len(case.steps)},
        "steps": [
            {
                "id": str(step.id),
                "key": step.step_key,
                "title": step.title,
                "owner_department": step.owner_department,
                "assignee": step.assignee.full_name if step.assignee else None,
                "task_id": str(step.task_id) if step.task_id else None,
                "due_date": step.due_date.isoformat() if step.due_date else None,
                "status": step.status,
            }
            for step in sorted(case.steps, key=lambda item: item.created_at or datetime.min.replace(tzinfo=timezone.utc))
        ],
    }
