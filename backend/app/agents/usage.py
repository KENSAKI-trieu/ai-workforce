"""Meter billed router and answer calls of the chat flows into LLMCostLog."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.models.models import User
from app.domains.platform.audit_service import log_llm_cost
from app.domains.platform.cost_calculator import UnsupportedModelPricingError
from app.agents.hr.llm_flow import UsageReporter

logger = logging.getLogger(__name__)


def _llm_usage_recorder(db: Session, user: User, agent_role: str) -> UsageReporter:
    """Meter every billed router or answer call into ``LLMCostLog``.

    The HR and Legal agents talk to the provider directly instead of going through the
    internal tool gateway, which is where every other agent's usage is recorded. Until
    this existed the cost dashboard reported those agents as free, while they were in
    fact making up to three calls per chat turn.

    The department is resolved from the user rather than passed in: ``log_llm_cost``
    validates an explicitly supplied department against a fixed list, so a tenant that
    named its departments anything else would have had its HR usage rejected instead of
    recorded.
    """

    def record(result: dict[str, Any]) -> None:
        usage = result.get("usage") or {}
        model_name = str(result.get("model") or "").strip()
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        # A provider that returned no counters cannot be priced without guessing, and a
        # zero-token row would only add noise to the dashboard.
        if not model_name or (prompt_tokens <= 0 and completion_tokens <= 0):
            return
        try:
            log_llm_cost(
                db,
                user.tenant_id,
                agent_role,
                model_name,
                prompt_tokens,
                completion_tokens,
                user_id=user.id,
                cached_prompt_tokens=int(usage.get("cached_prompt_tokens") or 0),
                usage_source="PROVIDER",
            )
        except UnsupportedModelPricingError:
            # Naming the model matters: the fix is a pricing row, not a code change.
            logger.warning(
                "%s LLM usage not metered: no pricing configured for model '%s'",
                agent_role,
                model_name,
            )
        except ValueError:
            logger.warning(
                "%s LLM usage not metered: rejected usage payload", agent_role, exc_info=True
            )
        except SQLAlchemyError:
            # The session is unusable after a failed flush, so hand back a clean one:
            # the answer this turn already produced still has to be persisted.
            logger.warning(
                "%s LLM usage not metered: database error", agent_role, exc_info=True
            )
            db.rollback()

    return record


def _hr_llm_usage_recorder(db: Session, user: User) -> UsageReporter:
    """The HR-scoped meter. Kept as its own name because the HR gate calls it by name."""
    return _llm_usage_recorder(db, user, "HR")
