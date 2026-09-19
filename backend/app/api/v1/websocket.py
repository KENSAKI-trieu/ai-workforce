"""
WebSocket Protocol Gateway for Real-Time Execution Streaming (LangGraph Execution Graph).
"""

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token
from app.models.models import AgentWorkflow, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws/v1", tags=["Real-time Streaming"])

# Browsers cannot set an Authorization header on a WebSocket handshake, so the token
# rides in the subprotocol -- `new WebSocket(url, ["bearer", accessToken])`. A `?token=`
# query parameter is accepted for non-browser clients, but it lands in access logs and
# proxy history, so the subprotocol is the form to use from the app.
_BEARER_SUBPROTOCOL = "bearer"


def _extract_token(websocket: WebSocket) -> str | None:
    header = websocket.headers.get("sec-websocket-protocol", "")
    protocols = [part.strip() for part in header.split(",") if part.strip()]
    if len(protocols) >= 2 and protocols[0].lower() == _BEARER_SUBPROTOCOL:
        return protocols[1]
    return websocket.query_params.get("token")


def _authenticate(websocket: WebSocket, db: Session, thread_id: str) -> User | None:
    """Resolve the caller, or None when the connection must be refused.

    The stream carries conversation content, so an unauthenticated listener who guesses
    a thread id must not get one. Every failure looks identical from the outside -- the
    client learns that the connection was refused, not which check refused it.
    """
    token = _extract_token(websocket)
    if not token:
        return None
    try:
        payload = decode_token(token)
    except HTTPException:
        return None
    if payload.get("type") != "access":
        return None
    try:
        user_id = uuid.UUID(str(payload.get("sub")))
    except (TypeError, ValueError):
        return None

    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        return None

    # The thread must belong to the caller's tenant. Without this the token of any
    # account in any company would open any other company's execution stream.
    workflow = db.query(AgentWorkflow).filter(
        AgentWorkflow.thread_id == thread_id,
        AgentWorkflow.tenant_id == user.tenant_id,
    ).first()
    if workflow is None:
        return None
    return user


@router.websocket("/execution/{thread_id}")
async def websocket_execution_stream(
    websocket: WebSocket,
    thread_id: str,
    db: Session = Depends(get_db),
):
    """
    Streams LangGraph DAG execution graph events to the visualizer UI in real-time.
    """
    user = _authenticate(websocket, db, thread_id)
    if user is None:
        # 1008 = policy violation. Closing before accept() rejects the handshake itself.
        await websocket.close(code=1008, reason="Not authorized for this execution thread")
        logger.warning("Rejected unauthorized WebSocket handshake for thread %s", thread_id)
        return

    subprotocol = (
        _BEARER_SUBPROTOCOL
        if websocket.headers.get("sec-websocket-protocol")
        else None
    )
    await websocket.accept(subprotocol=subprotocol)
    logger.info("WebSocket client %s connected for thread %s", user.id, thread_id)

    try:
        # Event 1: Start node transition
        await websocket.send_json({
            "event": "NODE_TRANSITION",
            "thread_id": thread_id,
            "data": {
                "from_node": "CEO_Orchestrator",
                "to_node": "HR_Agent",
                "reason": "Phát hiện nhu cầu khởi tạo hồ sơ nhân sự",
            },
        })
        await asyncio.sleep(0.5)

        # Event 2: Tool call start
        await websocket.send_json({
            "event": "TOOL_CALL_START",
            "thread_id": thread_id,
            "data": {
                "agent": "HR_Agent",
                "tool_name": "create_employee_record",
                "status": "EXECUTING",
            },
        })
        await asyncio.sleep(0.5)

        # Event 3: Task complete
        await websocket.send_json({
            "event": "TASK_COMPLETE",
            "thread_id": thread_id,
            "data": {
                "status": "COMPLETED",
                "summary": "Tất cả các nút DAG đã thực thi xong.",
            },
        })

        # Keep connection open for client echo
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(f"Echo: {data}")

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected for thread %s", thread_id)
