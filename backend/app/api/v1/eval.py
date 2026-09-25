"""
API Endpoints for RAGAS & RAG Quality Evaluation Benchmarking.
"""

from typing import Dict, Any, List
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user, RoleRequired
from app.models.models import User
from app.domains.knowledge.rag_service import hybrid_search_documents
from app.domains.platform.eval_service import evaluate_rag_quality

router = APIRouter(prefix="/eval", tags=["RAG Benchmark & Evaluation"])


class BenchmarkRequest(BaseModel):
    query: str = "Thời gian làm việc và số ngày phép năm 2025"


@router.post(
    "/benchmark",
    summary="Run RAGAS Quality Benchmark Evaluation for a RAG Query (CEO & Manager only)",
    dependencies=[Depends(RoleRequired("CEO", "Manager"))],
)
def run_rag_benchmark(
    req: BenchmarkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Dict[str, Any]:
    chunks = hybrid_search_documents(
        db,
        current_user.tenant_id,
        req.query,
        top_k=5,
        user_role=current_user.role,
        user_department=current_user.department,
    )
    sample_answer = chunks[0]["content"] if chunks else "Thông tin được cập nhật theo quy định công ty."
    
    scorecard = evaluate_rag_quality(req.query, chunks, sample_answer)
    return {
        "status": "COMPLETED",
        "scorecard": scorecard,
        "retrieved_chunks_count": len(chunks),
    }
