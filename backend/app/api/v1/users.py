"""User management and personal profile API routes (sync)."""

import uuid
from decimal import Decimal
from uuid import UUID
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user, RoleRequired
from app.models.models import User, UserProfile
from app.schemas.schemas import (
    EmploymentProfileUpdate,
    SelfProfileUpdate,
    UserResponse,
    UserUpdate,
)
from app.clients.cloudinary_service import upload_avatar
from app.domains.hr.hr_service import get_or_create_leave_balance, query_leave_balance

router = APIRouter(prefix="/users", tags=["Users"])
PERSONAL_PROFILE_FIELDS = {
    "phone", "address", "city", "country", "date_of_birth", "gender", "bio",
    "emergency_contact_name", "emergency_contact_phone", "preferences",
}
EMPLOYMENT_MANAGEMENT_ROLES = {"Owner", "CEO"}


def _get_or_create_profile(db: Session, user: User) -> UserProfile:
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
    )
    db.add(profile)
    db.flush()
    return profile


def _leave_balance(db: Session, user: User) -> dict:
    value = query_leave_balance(db, user)
    return {
        "total_days": value["total_days"],
        "used_days": value["used_days"],
        "remaining_days": value["remaining_days"],
    }


def _serialize_full_profile(db: Session, user: User, profile: UserProfile) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "department": user.department,
        "avatar_url": user.avatar_url,
        "is_active": user.is_active,
        "phone": profile.phone,
        "address": profile.address,
        "city": profile.city,
        "country": profile.country,
        "date_of_birth": profile.date_of_birth.isoformat() if profile.date_of_birth else None,
        "gender": profile.gender,
        "bio": profile.bio,
        "emergency_contact_name": profile.emergency_contact_name,
        "emergency_contact_phone": profile.emergency_contact_phone,
        "preferences": profile.preferences or {},
        "employment": {
            "job_title": profile.job_title,
            "employee_code": profile.employee_code,
            "hire_date": profile.hire_date.isoformat() if profile.hire_date else None,
            "employment_type": profile.employment_type,
            "employment_status": profile.employment_status,
            "manager": (
                {"id": str(user.manager.id), "name": user.manager.full_name}
                if user.manager else None
            ),
            "skills": profile.skills or [],
            "certifications": profile.certifications or [],
            "experience_summary": profile.experience_summary,
            "monthly_salary": float(profile.monthly_salary) if profile.monthly_salary is not None else None,
            "salary_currency": profile.salary_currency,
            "leave": _leave_balance(db, user),
        },
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
    }


@router.get("/me", response_model=UserResponse, summary="Get current user profile")
def get_my_profile(
    current_user: User = Depends(get_current_active_user),
) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.patch("/me", response_model=UserResponse, summary="Update own profile")
def update_my_profile(
    data: UserUpdate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> UserResponse:
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(current_user, field, value)
    db.add(current_user)
    return UserResponse.model_validate(current_user)


@router.get("/me/profile", summary="Get the current user's full profile")
def get_my_full_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    profile = _get_or_create_profile(db, current_user)
    return _serialize_full_profile(db, current_user, profile)


@router.patch("/me/profile", summary="Update the current user's personal profile")
def update_my_full_profile(
    data: SelfProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    profile = _get_or_create_profile(db, current_user)
    update_data = data.model_dump(exclude_unset=True)
    if "full_name" in update_data:
        current_user.full_name = update_data.pop("full_name").strip()
    for field, value in update_data.items():
        if field in PERSONAL_PROFILE_FIELDS:
            if field == "preferences" and value is not None:
                value = value.model_dump() if hasattr(value, "model_dump") else value
            setattr(profile, field, value)
    db.commit()
    db.refresh(current_user)
    db.refresh(profile)
    return _serialize_full_profile(db, current_user, profile)


@router.post("/me/avatar", summary="Upload the current user's avatar")
def upload_my_avatar(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    allowed_types = {"image/jpeg", "image/png", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=415, detail="Avatar must be JPEG, PNG or WebP")
    content = file.file.read(5 * 1024 * 1024 + 1)
    if not content:
        raise HTTPException(status_code=422, detail="Avatar file is empty")
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Avatar must be 5 MB or smaller")
    avatar_url = upload_avatar(
        content,
        tenant_id=str(current_user.tenant_id),
        user_id=str(current_user.id),
        content_type=file.content_type,
    )
    current_user.avatar_url = avatar_url
    db.commit()
    return {"avatar_url": avatar_url}


@router.patch("/{user_id}/employment", summary="Update protected employment information")
def update_employment_profile(
    user_id: UUID,
    data: EmploymentProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    is_hr_manager = (
        current_user.role in {"Manager", "Admin"}
        and current_user.department == "HR"
    )
    if current_user.role not in EMPLOYMENT_MANAGEMENT_ROLES and not is_hr_manager:
        raise HTTPException(status_code=403, detail="Only Owner/Admin/HR can update employment data")
    target_user = db.query(User).filter(
        User.id == user_id,
        User.tenant_id == current_user.tenant_id,
    ).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    profile = _get_or_create_profile(db, target_user)
    update_data = data.model_dump(exclude_unset=True)
    for field in ("job_title", "employee_code", "hire_date", "monthly_salary", "salary_currency"):
        if field in update_data:
            setattr(profile, field, update_data[field])

    if "leave_total_days" in update_data or "leave_used_days" in update_data:
        current_balance = _leave_balance(db, target_user)
        total = Decimal(str(update_data.get("leave_total_days", current_balance["total_days"])))
        used = Decimal(str(update_data.get("leave_used_days", current_balance["used_days"])))
        if used > total:
            raise HTTPException(status_code=422, detail="Used leave cannot exceed total leave")
        balance = get_or_create_leave_balance(db, target_user)
        balance.allocated_days = total
        balance.carried_over_days = Decimal("0.00")
        balance.used_days = used
        if Decimal(balance.reserved_days) > total - used:
            raise HTTPException(status_code=409, detail="Pending leave exceeds the adjusted balance")
    db.commit()
    return _serialize_full_profile(db, target_user, profile)


@router.get(
    "/",
    response_model=List[UserResponse],
    summary="List all users",
    dependencies=[Depends(RoleRequired("Owner", "Admin", "CEO", "Manager"))],
)
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> List[UserResponse]:
    query = db.query(User).filter(User.tenant_id == current_user.tenant_id)
    if current_user.role == "Manager":
        query = query.filter(User.department == current_user.department)
    users = query.all()
    return [UserResponse.model_validate(u) for u in users]


@router.get("/{user_id}", response_model=UserResponse, summary="Get user by ID")
def get_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> UserResponse:
    user = db.query(User).filter(User.id == user_id, User.tenant_id == current_user.tenant_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if current_user.role == "Manager" and user.department != current_user.department:
        raise HTTPException(status_code=404, detail="User not found")
    if current_user.role in {"Employee", "Guest"} and user.id != current_user.id:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse.model_validate(user)
