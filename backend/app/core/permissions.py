"""The permission vocabulary a tenant's positions grant.

A position carries two independent things. Its ``name`` is whatever the company calls
that job and may be renamed at any time; its ``permissions`` are codes from this module
and are what the code actually branches on. Splitting them is what makes a company-defined
org tree possible: renaming "Quản lý" to "Trưởng nhóm" must never change who can do what.

This module deliberately has no imports from ``app.services`` or ``app.models``. It is
loaded by seeding, by the request guards and by the API layer, and a dependency in any of
those directions would close an import cycle through ``app/services/__init__.py`` -- the
same reason ``app.core.hr_capabilities`` lives here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Permission:
    code: str
    group: str
    label: str
    description: str


# Ordered for display: the API and the permission-picker UI both render this list as-is.
PERMISSIONS: tuple[Permission, ...] = (
    # --- Tổ chức ---
    Permission(
        "org.structure.manage", "Tổ chức", "Quản lý cơ cấu chức vụ",
        "Tạo, đổi tên, sắp xếp và xoá chức vụ trong cây tổ chức.",
    ),
    Permission(
        "users.view", "Tổ chức", "Xem danh sách nhân sự",
        "Xem danh bạ nhân viên trong phạm vi được phép.",
    ),
    Permission(
        "users.manage", "Tổ chức", "Quản lý tài khoản nhân sự",
        "Tạo tài khoản, sửa thông tin và khoá hoặc mở khoá tài khoản.",
    ),
    Permission(
        "users.position.assign", "Tổ chức", "Gán chức vụ cho nhân sự",
        "Đặt một nhân viên vào một chức vụ trong cây tổ chức.",
    ),
    # --- Workspace ---
    Permission(
        "workspace.settings.manage", "Workspace", "Cấu hình workspace",
        "Sửa thông tin công ty, bảo mật, múi giờ và các thiết lập chung.",
    ),
    Permission(
        "workspace.delete", "Workspace", "Yêu cầu xoá workspace",
        "Khởi tạo yêu cầu xoá toàn bộ workspace. Nên giới hạn ở một chức vụ duy nhất.",
    ),
    # --- AI ---
    Permission(
        "agents.configure", "AI", "Cấu hình nhân viên AI",
        "Sửa system prompt, công cụ và quyền truy cập tri thức của các agent.",
    ),
    # --- Tri thức ---
    Permission(
        "knowledge.manage", "Tri thức", "Quản lý kho tri thức",
        "Tải lên, sửa và xoá tài liệu trong kho tri thức.",
    ),
    Permission(
        "knowledge.view_restricted", "Tri thức", "Xem tài liệu hạn chế",
        "Đọc được tài liệu đánh dấu hạn chế, bỏ qua danh sách chức vụ được phép.",
    ),
    # --- Nhân sự ---
    Permission(
        "hr.directory.view", "Nhân sự", "Tra cứu danh bạ nhân sự",
        "Dùng công cụ tra cứu danh bạ và tìm kiếm hồ sơ cơ bản.",
    ),
    Permission(
        "hr.employee.manage", "Nhân sự", "Quản lý hồ sơ lao động",
        "Sửa chức danh, mã nhân viên, ngày vào làm và cấp trên trực tiếp.",
    ),
    Permission(
        "hr.scope.company", "Nhân sự", "Phạm vi nhân sự toàn công ty",
        "Xem dữ liệu nhân sự của mọi phòng ban, không giới hạn theo cây báo cáo.",
    ),
    Permission(
        "hr.scope.reports", "Nhân sự", "Phạm vi cấp dưới",
        "Xem dữ liệu nhân sự của những người thuộc cây báo cáo bên dưới mình.",
    ),
    Permission(
        "hr.private.view", "Nhân sự", "Xem thông tin cá nhân",
        "Xem điện thoại, địa chỉ và liên hệ khẩn cấp của nhân viên khác.",
    ),
    Permission(
        "hr.compensation.view", "Nhân sự", "Xem lương",
        "Xem dữ liệu lương của nhân viên khác.",
    ),
    Permission(
        "hr.contract.view", "Nhân sự", "Xem hợp đồng",
        "Xem tóm tắt hợp đồng lao động và thời gian thử việc.",
    ),
    Permission(
        "hr.leave.view", "Nhân sự", "Xem dữ liệu nghỉ phép",
        "Xem quỹ phép và lịch sử nghỉ của nhân viên khác.",
    ),
    Permission(
        "hr.performance.view", "Nhân sự", "Xem đánh giá hiệu suất",
        "Xem dữ liệu đánh giá hiệu suất của nhân viên khác.",
    ),
    Permission(
        "hr.documents.view", "Nhân sự", "Xem hồ sơ tài liệu nhân sự",
        "Xem danh mục tài liệu đính kèm hồ sơ nhân viên.",
    ),
    Permission(
        "hr.discipline.view", "Nhân sự", "Xem hồ sơ kỷ luật",
        "Xem dữ liệu kỷ luật của nhân viên khác.",
    ),
    Permission(
        "hr.notes.view", "Nhân sự", "Xem ghi chú nội bộ nhân sự",
        "Xem ghi chú nội bộ do bộ phận nhân sự lưu về nhân viên.",
    ),
    # --- Phê duyệt ---
    Permission(
        "approvals.sign", "Phê duyệt", "Phê duyệt yêu cầu",
        "Duyệt hoặc từ chối yêu cầu trong phạm vi quản lý của mình.",
    ),
    Permission(
        "approvals.sign_critical", "Phê duyệt", "Phê duyệt yêu cầu tối quan trọng",
        "Duyệt cả những yêu cầu được đánh dấu mức rủi ro CRITICAL.",
    ),
    # --- Chuyên môn ---
    Permission(
        "finance.expense.view", "Chuyên môn", "Tra cứu chi phí",
        "Dùng công cụ tra cứu chi phí và dữ liệu tài chính.",
    ),
    Permission(
        "legal.document.generate", "Chuyên môn", "Soạn văn bản pháp lý",
        "Dùng công cụ sinh bản nháp văn bản pháp lý.",
    ),
    # --- Báo cáo ---
    Permission(
        "analytics.view", "Báo cáo", "Xem báo cáo vận hành",
        "Xem bảng phân tích và thống kê vận hành.",
    ),
    Permission(
        "costs.view", "Báo cáo", "Xem chi phí AI",
        "Xem báo cáo chi phí sử dụng mô hình.",
    ),
    Permission(
        "costs.manage", "Báo cáo", "Quản lý ngân sách AI",
        "Đặt ngân sách, cảnh báo và quy tắc định tuyến mô hình.",
    ),
    Permission(
        "audit.view", "Báo cáo", "Xem nhật ký kiểm toán",
        "Xem nhật ký kiểm toán trong phạm vi của mình.",
    ),
    Permission(
        "audit.view_all", "Báo cáo", "Xem toàn bộ nhật ký kiểm toán",
        "Xem nhật ký kiểm toán của toàn công ty.",
    ),
)

PERMISSION_CODES: frozenset[str] = frozenset(item.code for item in PERMISSIONS)

# Which permission opens each HR data section. This replaces a role x department matrix
# that hardcoded department codes like "FINANCE" -- a company that named its finance
# department anything else silently lost salary access, the same class of bug that
# renaming a role used to cause.
HR_SECTION_PERMISSIONS: dict[str, str] = {
    "BASIC": "hr.directory.view",
    "PRIVATE": "hr.private.view",
    "CONTRACT": "hr.contract.view",
    "COMPENSATION": "hr.compensation.view",
    "LEAVE": "hr.leave.view",
    "PERFORMANCE": "hr.performance.view",
    "DOCUMENTS": "hr.documents.view",
    "DISCIPLINE": "hr.discipline.view",
    "HR_NOTES": "hr.notes.view",
}

_BY_CODE: dict[str, Permission] = {item.code: item for item in PERMISSIONS}


def permission_exists(code: str) -> bool:
    return code in _BY_CODE


def normalize_permissions(codes) -> list[str]:
    """Keep only codes this build defines, de-duplicated and ordered for stable storage.

    Unknown codes are dropped rather than rejected: a tenant row written by a newer build
    must not break an older one, and a code removed from the vocabulary must not leave a
    position unreadable.
    """
    seen = {str(code).strip() for code in (codes or [])}
    return sorted(code for code in seen if code in _BY_CODE)


def permission_catalog() -> list[dict[str, str]]:
    """The vocabulary as the API returns it, for the permission picker."""
    return [
        {
            "code": item.code,
            "group": item.group,
            "label": item.label,
            "description": item.description,
        }
        for item in PERMISSIONS
    ]


# ---------------------------------------------------------------------------
# Default tree seeded for every tenant
# ---------------------------------------------------------------------------
# Slugs are the lowercased legacy role names on purpose. Document ACLs store role strings
# as data (DocumentChunk.allowed_roles) and match them lowercased, so keeping these slugs
# means every already-granted document keeps matching after the migration. Slugs never
# change; only `name` does.
#
# The permission sets below reproduce today's hardcoded role sets. They are not consulted
# by any guard yet -- phase 1 changes no behavior -- and each membership gets pinned by a
# test as its call site moves over in phase 2.

# Powers that have nothing to do with reading employee records. Kept separate so the
# department-specialised jobs below can combine them with a different section set instead
# of having to subtract sections back out.
_MANAGER_CORE = (
    "users.view",
    "hr.directory.view",
    "hr.scope.reports",
    "knowledge.manage",
    "approvals.sign",
    "analytics.view",
    "costs.view",
    "audit.view",
    "finance.expense.view",
)

_ADMIN_CORE = _MANAGER_CORE + (
    "users.manage",
    "users.position.assign",
    "workspace.settings.manage",
    "agents.configure",
    "knowledge.view_restricted",
    "approvals.sign_critical",
    "costs.manage",
    "audit.view_all",
    "legal.document.generate",
)

# The sections a line manager could read about their reports.
_LINE_MANAGER_SECTIONS = (
    "hr.contract.view",
    "hr.leave.view",
    "hr.performance.view",
)

_MANAGER_PERMISSIONS = _MANAGER_CORE + _LINE_MANAGER_SECTIONS
_ADMIN_PERMISSIONS = _ADMIN_CORE + _LINE_MANAGER_SECTIONS

_ALL_SECTION_PERMISSIONS = tuple(sorted(set(HR_SECTION_PERMISSIONS.values())))

# CEO differs from the root only by not being able to request workspace deletion, which
# matches today's Owner-only guard in workspace.py.
_CEO_PERMISSIONS = _ADMIN_CORE + _ALL_SECTION_PERMISSIONS + (
    "org.structure.manage",
    "hr.scope.company",
    "hr.employee.manage",
)


@dataclass(frozen=True)
class DefaultPosition:
    slug: str
    name: str
    legacy_role: str
    parent_slug: str | None
    permissions: tuple[str, ...]
    grants_all: bool = False


# Ordered parents-first so a single pass can resolve parent_slug.
#
# The root is displayed as "CEO" because the person who creates the company holds it, and
# that is what they expect to be called. Its slug stays `owner` so the legacy role string
# already stored in document ACLs keeps matching. Name and slug are allowed to disagree --
# that separation is the whole point of the model.
DEFAULT_POSITIONS: tuple[DefaultPosition, ...] = (
    DefaultPosition("owner", "CEO", "Owner", None, (), grants_all=True),
    # Hosts users whose legacy role string is literally "CEO". A brand-new company never
    # has one, so for them this is just a spare node they can rename or delete.
    DefaultPosition("ceo", "Giám đốc điều hành", "CEO", "owner", _CEO_PERMISSIONS),
    DefaultPosition("admin", "Quản trị viên", "Admin", "ceo", _ADMIN_PERMISSIONS),
    DefaultPosition("manager", "Quản lý", "Manager", "ceo", _MANAGER_PERMISSIONS),
    DefaultPosition("employee", "Nhân viên", "Employee", "manager", ()),
    DefaultPosition("guest", "Khách", "Guest", "manager", ()),
)

# Legacy User.role value -> slug of the position that reproduces it.
LEGACY_ROLE_TO_SLUG: dict[str, str] = {
    item.legacy_role: item.slug for item in DEFAULT_POSITIONS
}

ROOT_POSITION_SLUG = "owner"


@dataclass(frozen=True)
class SpecialisedPosition:
    """A job the old policy expressed by cross-multiplying a role with a department code.

    `_role_sections` used to read both `actor.role` and `actor.department`, so an Admin in
    HR and an Admin in IT were different jobs that no single position could represent.
    They are split into real positions rather than folded together, because folding would
    either strip HR of data it has today or hand Finance data it never had.

    `legacy_department` is matched against the literal department code the old rule used.
    A tenant that named its finance department something else never matched that rule, so
    its managers stay on the generic position -- which is exactly what happens today.
    """

    slug: str
    name: str
    parent_slug: str
    legacy_role: str
    legacy_department: str
    permissions: tuple[str, ...]


SPECIALISED_POSITIONS: tuple[SpecialisedPosition, ...] = (
    SpecialisedPosition(
        "hr-admin", "Quản trị viên Nhân sự", "ceo", "Admin", "HR",
        _ADMIN_CORE + _ALL_SECTION_PERMISSIONS + ("hr.employee.manage",),
    ),
    SpecialisedPosition(
        "hr-manager", "Quản lý Nhân sự", "ceo", "Manager", "HR",
        _MANAGER_CORE + _LINE_MANAGER_SECTIONS + (
            "hr.employee.manage",
            "hr.private.view",
            "hr.documents.view",
        ),
    ),
    # Finance reads salary and nothing else personal. This is NOT a superset of the line
    # manager set: it deliberately drops contract, leave and performance.
    SpecialisedPosition(
        "finance-admin", "Quản trị viên Tài chính", "ceo", "Admin", "FINANCE",
        _ADMIN_CORE + ("hr.compensation.view",),
    ),
    SpecialisedPosition(
        "finance-manager", "Quản lý Tài chính", "ceo", "Manager", "FINANCE",
        _MANAGER_CORE + ("hr.compensation.view",),
    ),
)

# (legacy role, legacy department) -> slug, for the backfill.
LEGACY_SPECIALISATIONS: dict[tuple[str, str], str] = {
    (item.legacy_role, item.legacy_department): item.slug
    for item in SPECIALISED_POSITIONS
}

# The permissions without which a company can no longer administer itself. At least one
# active user must always hold both, or the tenant locks itself out with no way back.
SELF_LOCKOUT_GUARD_PERMISSIONS: frozenset[str] = frozenset({
    "org.structure.manage",
    "users.position.assign",
})
