"""V1 API main router — aggregates all sub-routers."""

from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.users import router as users_router
from app.api.v1.agents import router as agents_router
from app.api.v1.chat import router as chat_router
from app.api.v1.documents import router as documents_router
from app.api.v1.approvals import router as approvals_router
from app.api.v1.specialized import router as specialized_router
from app.api.v1.audit import router as audit_router
from app.api.v1.websocket import router as ws_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.eval import router as eval_router
from app.api.v1.tasks import router as tasks_router
from app.api.v1.users_mgmt import router as users_mgmt_router
from app.api.v1.costs import router as costs_router
from app.api.v1.workflows import router as workflows_router
from app.api.v1.workspace import router as workspace_router
from app.api.v1.management import router as management_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.integrations import router as integrations_router
from app.api.v1.customer_support import router as customer_support_router
from app.api.v1.hr import router as hr_router
from app.api.v1.tool_gateway import router as tool_gateway_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(agents_router)
api_router.include_router(chat_router)
api_router.include_router(documents_router)
api_router.include_router(approvals_router)
api_router.include_router(specialized_router)
api_router.include_router(audit_router)
api_router.include_router(ws_router)
api_router.include_router(eval_router)
api_router.include_router(dashboard_router)
api_router.include_router(tasks_router)
api_router.include_router(users_mgmt_router)
api_router.include_router(costs_router)
api_router.include_router(workflows_router)
api_router.include_router(workspace_router)
api_router.include_router(management_router)
api_router.include_router(notifications_router)
api_router.include_router(integrations_router)
api_router.include_router(customer_support_router)
api_router.include_router(hr_router)
api_router.include_router(tool_gateway_router)
