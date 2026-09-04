"""
Seed script: Tạo công ty test với:
  - Tenant: Test Company
  - CEO: test@gmail.com / 123456 (giữ chức vụ gốc, giống hệt người tạo công ty thật)
  - Nhân viên: test1@gmail.com -> test40@gmail.com / 123456 (mỗi người thuộc dept khác nhau)

Script này tạo bảng bằng `create_all` chứ không đi qua Alembic. Nếu database đã có schema
cũ, hãy chạy `alembic upgrade head` trước để có đủ các cột mà script này ghi vào.
"""

import sys
import uuid
import logging
from app.core.database import sync_engine, Base, SyncSessionLocal
from app.core.permissions import ROOT_POSITION_SLUG
from app.core.security import get_password_hash
from app.core.hr_capabilities import HR_CONFIGURATION_VERSION, default_hr_tools
from app.models.models import Tenant, User, AIAgent, UserMemory
from app.services.position_service import (
    assign_position,
    backfill_tenant_user_positions,
    ensure_tenant_positions,
)
import json

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("seed_test_company")

# Phân bổ department cho 40 nhân viên
DEPARTMENTS = ["IT", "HR", "FINANCE", "SALES", "LEGAL", "IT", "HR", "FINANCE"]

def seed():
    # Đảm bảo bảng tồn tại
    Base.metadata.create_all(bind=sync_engine)

    db = SyncSessionLocal()
    try:
        # ────────────────────────────────────────────
        # 1. Tenant
        # ────────────────────────────────────────────
        tenant = db.query(Tenant).filter(Tenant.domain == "testcompany.local").first()
        if not tenant:
            tenant = Tenant(
                id=uuid.uuid4(),
                name="Test Company",
                domain="testcompany.local",
            )
            db.add(tenant)
            db.commit()
            db.refresh(tenant)
            logger.info(f"✅ Tạo Tenant: {tenant.name} (id={tenant.id})")
        else:
            logger.info(f"ℹ️  Tenant đã tồn tại: {tenant.name}")

        # ────────────────────────────────────────────
        # 2. Cây chức vụ + CEO
        # ────────────────────────────────────────────
        # Quyền được đọc qua Position, không phải qua chuỗi role. Một user không có
        # position_id thì `user_permissions()` trả về rỗng — nên phải seed cây chức vụ
        # trước và gán chức vụ gốc cho CEO, đúng như `auth_service.register_user` làm.
        positions = ensure_tenant_positions(db, tenant.id)
        db.commit()
        root_position = positions[ROOT_POSITION_SLUG]

        ceo_email = "test@gmail.com"
        ceo = db.query(User).filter(User.email == ceo_email).first()
        if not ceo:
            ceo = User(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                email=ceo_email,
                full_name="CEO Test",
                password_hash=get_password_hash("123456"),
                role="CEO",
                department="BOARD",
            )
            db.add(ceo)
            db.flush()
            logger.info(f"✅ Tạo CEO: {ceo_email}")
        else:
            logger.info(f"ℹ️  CEO đã tồn tại: {ceo_email}")
        # Chạy cả trên tài khoản đã tồn tại: bản seed cũ để position_id = NULL, nên chạy
        # lại script là cách vá dữ liệu đó.
        assign_position(db, ceo, root_position)
        db.commit()
        db.refresh(ceo)
        logger.info(f"   ↳ chức vụ: {root_position.name} | role: {ceo.role}")

        # ────────────────────────────────────────────
        # 3. 40 nhân viên test1 -> test40
        # ────────────────────────────────────────────
        for i in range(1, 41):
            email = f"test{i}@gmail.com"
            existing = db.query(User).filter(User.email == email).first()
            if existing:
                logger.info(f"  ↩️  Bỏ qua (đã tồn tại): {email}")
                continue

            dept = DEPARTMENTS[(i - 1) % len(DEPARTMENTS)]
            role = "Manager" if i % 8 == 0 else "Employee"  # mỗi 8 người có 1 manager

            user = User(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                email=email,
                full_name=f"Nhân Viên Test {i}",
                password_hash=get_password_hash("123456"),
                role=role,
                department=dept,
            )
            db.add(user)

            # Seed leave balance cho mỗi nhân viên
            db.flush()  # lấy id
            mem = UserMemory(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                user_id=user.id,
                memory_category="hr",
                memory_key="leave_balance",
                memory_value=json.dumps({
                    "total_days": 12,
                    "used_days": 0,
                    "remaining_days": 12
                }),
                confidence_score=1.0,
            )
            db.add(mem)
            logger.info(f"  ✅ {email} | {role} | {dept}")

        db.flush()
        # Map role legacy -> chức vụ mặc định cho mọi user chưa có position_id, kể cả
        # những người do lần seed trước tạo ra.
        assigned = backfill_tenant_user_positions(db, tenant.id)
        if assigned:
            logger.info(f"  ✅ Gán chức vụ cho {assigned} tài khoản chưa có")
        db.commit()

        # ────────────────────────────────────────────
        # 4. AI Agents cho tenant Test Company
        # ────────────────────────────────────────────
        agents_data = [
            {"role_code": "CEO",       "name": "CEO Master Agent",       "avatar_emoji": "👔", "model_name": "gpt-4o"},
            {"role_code": "HR",        "name": "HR AI Employee",         "avatar_emoji": "🧑‍💼", "model_name": "gpt-4o"},
            {"role_code": "KNOWLEDGE", "name": "Knowledge Base AI",      "avatar_emoji": "📚", "model_name": "gpt-4o"},
            {"role_code": "LEGAL",     "name": "Legal Counsel AI",       "avatar_emoji": "⚖️",  "model_name": "gpt-4o"},
            {"role_code": "IT",        "name": "IT Support AI",          "avatar_emoji": "💻", "model_name": "gpt-4o"},
            {"role_code": "FINANCE",   "name": "Finance & Accounting AI","avatar_emoji": "💰", "model_name": "gpt-4o"},
            {"role_code": "SALES",     "name": "Sales & CRM AI",         "avatar_emoji": "📈", "model_name": "gpt-4o"},
        ]

        for adata in agents_data:
            agent = db.query(AIAgent).filter(
                AIAgent.tenant_id == tenant.id,
                AIAgent.role_code == adata["role_code"]
            ).first()
            if not agent:
                agent = AIAgent(
                    id=uuid.uuid4(),
                    tenant_id=tenant.id,
                    role_code=adata["role_code"],
                    name=adata["name"],
                    avatar_emoji=adata["avatar_emoji"],
                    model_name=adata["model_name"],
                    description=f"{adata['name']} cho Test Company",
                    system_prompt=f"You are the {adata['name']} AI Agent.",
                    is_active=True,
                    # Derived from the executor's capability list rather than copied: this
                    # list still granted the pre-split `get_employee_profile` name and two
                    # capabilities the migration revokes on the first chat turn.
                    tools_access=(
                        default_hr_tools() if adata["role_code"] == "HR" else []
                    ),
                    allowed_actions=(
                        default_hr_tools() if adata["role_code"] == "HR" else []
                    ),
                    configuration_version=HR_CONFIGURATION_VERSION,
                )
                db.add(agent)
                logger.info(f"  ✅ Agent: {adata['role_code']}")
        db.commit()

        logger.info("\n🎉 Seed hoàn tất!")
        logger.info(f"   Tenant : Test Company (domain=testcompany.local)")
        logger.info(f"   CEO    : test@gmail.com / 123456")
        logger.info(f"   NV     : test1@gmail.com -> test40@gmail.com / 123456")

    except Exception as e:
        db.rollback()
        logger.error(f"❌ Lỗi: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
