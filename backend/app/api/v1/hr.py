"""AI HR V1 APIs with tenant, self, reporting-line and HR scopes."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user
from app.models.models import (
    EmploymentContract,
    HRCalendarEvent,
    LeaveRequest,
    OnboardingCase,
    Task,
    User,
    UserProfile,
)
from app.domains.platform.audit_events import add_audit_event
from app.domains.hr.hr_service import (
    can_manage_hr,
    can_view_employee,
    create_leave_request,
    create_onboarding_case,
    hr_scope_label,
    query_leave_balance,
    scoped_employee_query,
    serialize_contract,
    serialize_leave_request,
    serialize_onboarding,
)
from app.domains.platform.notification_service import create_notification
from app.domains.hr.hr_export_service import create_hr_directory_export

router = APIRouter(prefix="/hr", tags=["AI HR Operations"])


@router.get("/employees/export", summary="Export the authorized HR directory")
def export_employee_directory(
    format: Literal["xlsx", "pdf", "json"] = Query("xlsx"),
    directory: Literal["employees", "managers"] = Query("employees"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    export = create_hr_directory_export(
        db,
        actor=current_user,
        export_format=format,
        directory_type=directory,
    )
    return Response(
        content=export["content"],
        media_type=export["media_type"],
        headers={
            "Content-Disposition": f'attachment; filename="{export["filename"]}"',
            "Content-Length": str(len(export["content"])),
            "X-Content-Type-Options": "nosniff",
        },
    )


class LeaveRequestCreate(BaseModel):
    employee_id: uuid.UUID | None = None
    leave_type: Literal["ANNUAL", "SICK", "UNPAID"] = "ANNUAL"
    start_date: date
    end_date: date
    part_of_day: Literal["FULL_DAY", "MORNING", "AFTERNOON"] = "FULL_DAY"
    reason: str = Field(min_length=2, max_length=2000)


class ContractCreate(BaseModel):
    employee_id: uuid.UUID
    contract_number: str = Field(min_length=2, max_length=100)
    contract_type: Literal["PROBATION", "FIXED_TERM", "INDEFINITE", "PART_TIME"]
    start_date: date
    end_date: date | None = None
    probation_end_date: date | None = None
    signed_by_employee: bool = False
    signed_by_company: bool = False
    document_name: str | None = Field(None, max_length=255)
    document_url: str | None = Field(None, max_length=2000)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if self.probation_end_date and self.probation_end_date < self.start_date:
            raise ValueError("probation_end_date must be on or after start_date")
        return self


class OnboardingCreate(BaseModel):
    employee_id: uuid.UUID
    start_date: date
    probation_end_date: date | None = None
    mentor_id: uuid.UUID | None = None


class EmploymentScopeUpdate(BaseModel):
    manager_id: uuid.UUID | None = None
    employment_type: Literal["FULL_TIME", "PART_TIME", "CONTRACTOR", "INTERN"] | None = None
    employment_status: Literal["PROBATION", "OFFICIAL", "NOTICE", "TERMINATED"] | None = None
    skills: list[str] | None = Field(None, max_length=100)
    certifications: list[str] | None = Field(None, max_length=100)
    experience_summary: str | None = Field(None, max_length=5000)


def _employee_or_404(db: Session, current_user: User, employee_id: uuid.UUID) -> User:
    employee = db.query(User).filter(
        User.id == employee_id,
        User.tenant_id == current_user.tenant_id,
    ).first()
    if not employee or not can_view_employee(db, current_user, employee):
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


def _profile(db: Session, user: User) -> UserProfile:
    profile = db.query(UserProfile).filter(
        UserProfile.tenant_id == user.tenant_id,
        UserProfile.user_id == user.id,
    ).first()
    if profile:
        return profile
    profile = UserProfile(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        preferences={},
        salary_currency="VND",
        skills=[],
        certifications=[],
        employment_history=[],
    )
    db.add(profile)
    db.flush()
    return profile


def _serialize_employee(db: Session, viewer: User, employee: User) -> dict:
    profile = _profile(db, employee)
    result = {
        "id": str(employee.id),
        "full_name": employee.full_name,
        "email": employee.email,
        "role": employee.role,
        "department": employee.department,
        "manager": (
            {"id": str(employee.manager.id), "name": employee.manager.full_name}
            if employee.manager else None
        ),
        "job_title": profile.job_title,
        "employee_code": profile.employee_code,
        "hire_date": profile.hire_date.isoformat() if profile.hire_date else None,
        "employment_type": profile.employment_type,
        "employment_status": profile.employment_status,
        "skills": profile.skills or [],
        "certifications": profile.certifications or [],
        "experience_summary": profile.experience_summary,
        "leave": query_leave_balance(db, employee),
    }
    if employee.id == viewer.id or can_manage_hr(viewer):
        result["personal"] = {
            "phone": profile.phone,
            "address": profile.address,
            "date_of_birth": profile.date_of_birth.isoformat() if profile.date_of_birth else None,
            "emergency_contact_name": profile.emergency_contact_name,
            "emergency_contact_phone": profile.emergency_contact_phone,
        }
    return result


@router.get("/overview", summary="Role-scoped HR operations summary")
def get_hr_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    today = date.today()
    employees = scoped_employee_query(db, current_user).all()
    employee_ids = [employee.id for employee in employees]
    requests = db.query(LeaveRequest).filter(
        LeaveRequest.tenant_id == current_user.tenant_id,
        LeaveRequest.employee_id.in_(employee_ids),
    ) if employee_ids else db.query(LeaveRequest).filter(False)
    onboarding = db.query(OnboardingCase).filter(
        OnboardingCase.tenant_id == current_user.tenant_id,
        OnboardingCase.employee_id.in_(employee_ids),
    ) if employee_ids else db.query(OnboardingCase).filter(False)
    contracts = db.query(EmploymentContract).filter(
        EmploymentContract.tenant_id == current_user.tenant_id,
        EmploymentContract.employee_id.in_(employee_ids),
    ) if employee_ids else db.query(EmploymentContract).filter(False)
    return {
        "scope": hr_scope_label(current_user),
        "employees": len(employees),
        "pending_leave_requests": requests.filter(LeaveRequest.status == "WAITING").count(),
        "approved_leave_requests": requests.filter(LeaveRequest.status == "APPROVED").count(),
        "active_onboarding": onboarding.filter(OnboardingCase.status == "IN_PROGRESS").count(),
        "contracts_expiring_30d": contracts.filter(
            EmploymentContract.status == "ACTIVE",
            EmploymentContract.end_date >= today,
            EmploymentContract.end_date <= today + timedelta(days=30),
        ).count(),
        "probations_ending_7d": contracts.filter(
            EmploymentContract.status == "ACTIVE",
            EmploymentContract.probation_end_date >= today,
            EmploymentContract.probation_end_date <= today + timedelta(days=7),
        ).count(),
        "my_leave": query_leave_balance(db, current_user),
    }


@router.get("/employees", summary="List employees in the authorized HR scope")
def list_hr_employees(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    employees = scoped_employee_query(db, current_user).order_by(User.full_name).all()
    return [_serialize_employee(db, current_user, employee) for employee in employees]


@router.get("/employees/{employee_id}", summary="Read an authorized employee HR profile")
def get_hr_employee(
    employee_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return _serialize_employee(db, current_user, _employee_or_404(db, current_user, employee_id))


@router.patch("/employees/{employee_id}/employment", summary="Update HR employment scope data")
def update_hr_employment(
    employee_id: uuid.UUID,
    payload: EmploymentScopeUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not can_manage_hr(current_user):
        raise HTTPException(status_code=403, detail="Only HR can update employment records")
    employee = db.query(User).filter(
        User.id == employee_id,
        User.tenant_id == current_user.tenant_id,
    ).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    profile = _profile(db, employee)
    changes = payload.model_dump(exclude_unset=True)
    before = {
        "manager_id": str(employee.manager_id) if employee.manager_id else None,
        "employment_status": profile.employment_status,
        "employment_type": profile.employment_type,
    }
    if "manager_id" in changes:
        manager_id = changes.pop("manager_id")
        if manager_id == employee.id:
            raise HTTPException(status_code=422, detail="Employee cannot manage themselves")
        if manager_id:
            manager = db.query(User).filter(
                User.id == manager_id,
                User.tenant_id == current_user.tenant_id,
                User.is_active.is_(True),
            ).first()
            if not manager or manager.role not in {"Manager", "Owner", "CEO"}:
                raise HTTPException(status_code=422, detail="manager_id is not an eligible manager")
        employee.manager_id = manager_id
    for field, value in changes.items():
        setattr(profile, field, value)
    add_audit_event(
        db,
        tenant_id=current_user.tenant_id,
        actor_user=current_user,
        agent_role="HR",
        action="hr.employee.updated",
        resource_type="EMPLOYEE",
        resource_id=str(employee.id),
        before_data=before,
        after_data=payload.model_dump(exclude_unset=True, mode="json"),
        request=request,
    )
    db.commit()
    return _serialize_employee(db, current_user, employee)


@router.get("/leave-balance", summary="Get current user's structured leave balance")
def get_my_leave_balance(
    year: int | None = Query(None, ge=2000, le=2200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return query_leave_balance(db, current_user, year)


@router.post("/leave-requests", status_code=201, summary="Create a leave request requiring approval")
def submit_leave_request(
    payload: LeaveRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    employee = current_user
    if payload.employee_id and payload.employee_id != current_user.id:
        if not can_manage_hr(current_user):
            raise HTTPException(status_code=403, detail="Cannot create leave for another employee")
        employee = db.query(User).filter(
            User.id == payload.employee_id,
            User.tenant_id == current_user.tenant_id,
        ).first()
        if not employee:
            raise HTTPException(status_code=404, detail="Employee not found")
    item = create_leave_request(
        db,
        employee,
        start_date=payload.start_date,
        end_date=payload.end_date,
        leave_type=payload.leave_type,
        part_of_day=payload.part_of_day,
        reason=payload.reason,
    )
    return serialize_leave_request(item)


@router.get("/leave-requests", summary="List leave requests in the authorized scope")
def list_leave_requests(
    status: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    visible_ids = [employee.id for employee in scoped_employee_query(db, current_user).all()]
    query = db.query(LeaveRequest).filter(
        LeaveRequest.tenant_id == current_user.tenant_id,
        LeaveRequest.employee_id.in_(visible_ids),
    )
    if status:
        query = query.filter(LeaveRequest.status == status.upper())
    return [serialize_leave_request(item) for item in query.order_by(LeaveRequest.created_at.desc()).all()]


@router.get("/calendar-events", summary="List authorized internal HR calendar events")
def list_hr_calendar_events(
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    visible_ids = [employee.id for employee in scoped_employee_query(db, current_user).all()]
    query = db.query(HRCalendarEvent).filter(
        HRCalendarEvent.tenant_id == current_user.tenant_id,
        HRCalendarEvent.user_id.in_(visible_ids),
    )
    if start:
        query = query.filter(HRCalendarEvent.end_at >= datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc))
    if end:
        query = query.filter(HRCalendarEvent.start_at < datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc))
    return [
        {
            "id": str(item.id),
            "event_type": item.event_type,
            "title": item.title,
            "user": {"id": str(item.user.id), "name": item.user.full_name},
            "start_at": item.start_at.isoformat(),
            "end_at": item.end_at.isoformat(),
            "all_day": item.all_day,
            "sync_status": item.sync_status,
            "source_type": item.source_type,
            "source_id": item.source_id,
        }
        for item in query.order_by(HRCalendarEvent.start_at).all()
    ]


@router.post("/contracts", status_code=201, summary="Create an employment contract record")
def create_contract(
    payload: ContractCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not can_manage_hr(current_user):
        raise HTTPException(status_code=403, detail="Only HR can create contracts")
    employee = db.query(User).filter(
        User.id == payload.employee_id,
        User.tenant_id == current_user.tenant_id,
    ).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    exists = db.query(EmploymentContract).filter(
        EmploymentContract.tenant_id == current_user.tenant_id,
        EmploymentContract.contract_number == payload.contract_number,
    ).first()
    if exists:
        raise HTTPException(status_code=409, detail="Contract number already exists")
    contract = EmploymentContract(
        id=uuid.uuid4(),
        tenant_id=current_user.tenant_id,
        employee_id=employee.id,
        contract_number=payload.contract_number.strip(),
        contract_type=payload.contract_type,
        status="ACTIVE",
        start_date=payload.start_date,
        end_date=payload.end_date,
        probation_end_date=payload.probation_end_date,
        signed_by_employee=payload.signed_by_employee,
        signed_by_company=payload.signed_by_company,
        document_name=payload.document_name,
        document_url=payload.document_url,
        metadata_={},
        created_by_id=current_user.id,
    )
    db.add(contract)
    db.flush()
    add_audit_event(
        db,
        tenant_id=current_user.tenant_id,
        actor_user=current_user,
        agent_role="HR",
        action="hr.contract.created",
        tool_name="create_employment_contract",
        resource_type="EMPLOYMENT_CONTRACT",
        resource_id=str(contract.id),
        after_data=payload.model_dump(mode="json"),
        request=request,
    )
    db.commit()
    return serialize_contract(contract)


@router.get("/contracts", summary="List authorized employment contract records")
def list_contracts(
    expiring_days: int | None = Query(None, ge=0, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    visible_ids = [employee.id for employee in scoped_employee_query(db, current_user).all()]
    query = db.query(EmploymentContract).filter(
        EmploymentContract.tenant_id == current_user.tenant_id,
        EmploymentContract.employee_id.in_(visible_ids),
    )
    if expiring_days is not None:
        today = date.today()
        query = query.filter(
            EmploymentContract.end_date >= today,
            EmploymentContract.end_date <= today + timedelta(days=expiring_days),
        )
    items = []
    for contract in query.order_by(EmploymentContract.end_date.asc().nullslast()).all():
        item = serialize_contract(contract)
        if not (contract.employee_id == current_user.id or can_manage_hr(current_user)):
            item["document_url"] = None
        items.append(item)
    return items


@router.post("/onboarding", status_code=201, summary="Create the default onboarding workflow and tasks")
def start_onboarding(
    payload: OnboardingCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not can_manage_hr(current_user):
        raise HTTPException(status_code=403, detail="Only HR can start onboarding")
    employee = db.query(User).filter(
        User.id == payload.employee_id,
        User.tenant_id == current_user.tenant_id,
    ).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    if payload.mentor_id:
        mentor = db.query(User).filter(
            User.id == payload.mentor_id,
            User.tenant_id == current_user.tenant_id,
            User.is_active.is_(True),
        ).first()
        if not mentor:
            raise HTTPException(status_code=422, detail="Mentor is not in this workspace")
    case = create_onboarding_case(
        db,
        employee=employee,
        creator=current_user,
        start_date=payload.start_date,
        probation_end_date=payload.probation_end_date,
        mentor_id=payload.mentor_id,
    )
    return serialize_onboarding(case)


@router.get("/onboarding", summary="List onboarding cases in the authorized scope")
def list_onboarding(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    visible_ids = [employee.id for employee in scoped_employee_query(db, current_user).all()]
    query = db.query(OnboardingCase).filter(
        OnboardingCase.tenant_id == current_user.tenant_id,
        OnboardingCase.employee_id.in_(visible_ids),
    )
    return [serialize_onboarding(item) for item in query.order_by(OnboardingCase.created_at.desc()).all()]


@router.post("/reminders/scan", summary="Create deduplicated contract and probation reminders")
def scan_hr_reminders(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not can_manage_hr(current_user):
        raise HTTPException(status_code=403, detail="Only HR can scan HR reminders")
    today = date.today()
    contracts = db.query(EmploymentContract).filter(
        EmploymentContract.tenant_id == current_user.tenant_id,
        EmploymentContract.status == "ACTIVE",
        or_(
            EmploymentContract.end_date.between(today, today + timedelta(days=30)),
            EmploymentContract.probation_end_date.between(today, today + timedelta(days=7)),
        ),
    ).all()
    hr_users = db.query(User).filter(
        User.tenant_id == current_user.tenant_id,
        User.department == "HR",
        User.is_active.is_(True),
    ).all()
    created = 0
    for contract in contracts:
        reminder_type = (
            "PROBATION_ENDING"
            if contract.probation_end_date and contract.probation_end_date <= today + timedelta(days=7)
            else "CONTRACT_EXPIRING"
        )
        target_date = contract.probation_end_date if reminder_type == "PROBATION_ENDING" else contract.end_date
        recipients = [*hr_users]
        if contract.employee.manager and contract.employee.manager not in recipients:
            recipients.append(contract.employee.manager)
        for recipient in recipients:
            notification = create_notification(
                db,
                user=recipient,
                event_type=reminder_type,
                title="Thử việc sắp kết thúc" if reminder_type == "PROBATION_ENDING" else "Hợp đồng sắp hết hạn",
                message=f"{contract.employee.full_name} — {target_date:%d/%m/%Y}",
                severity="WARNING",
                entity_type="EMPLOYMENT_CONTRACT",
                entity_id=str(contract.id),
                dedup_key=f"{reminder_type.lower()}:{contract.id}:{target_date}",
            )
            created += int(notification is not None)
        task_exists = db.query(Task).filter(
            Task.tenant_id == current_user.tenant_id,
            Task.description == f"HR_REMINDER:{reminder_type}:{contract.id}:{target_date}",
        ).first()
        if not task_exists:
            db.add(Task(
                id=uuid.uuid4(),
                tenant_id=current_user.tenant_id,
                title=(
                    f"Đánh giá thử việc — {contract.employee.full_name}"
                    if reminder_type == "PROBATION_ENDING"
                    else f"Xem xét gia hạn hợp đồng — {contract.employee.full_name}"
                ),
                description=f"HR_REMINDER:{reminder_type}:{contract.id}:{target_date}",
                creator_id=current_user.id,
                assignee_id=contract.employee.manager_id or current_user.id,
                priority="HIGH",
                due_date=datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc),
                status="PENDING",
                attachments=[],
            ))
    add_audit_event(
        db,
        tenant_id=current_user.tenant_id,
        actor_user=current_user,
        agent_role="HR",
        action="hr.reminders.scanned",
        tool_name="get_contract_expiry",
        resource_type="EMPLOYMENT_CONTRACT",
        output_result={"matched_contracts": len(contracts), "notifications_created": created},
        request=request,
    )
    db.commit()
    return {"matched_contracts": len(contracts), "notifications_created": created}
    hr_scope_label,
