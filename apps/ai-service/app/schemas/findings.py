from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.citations import Citation


Severity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class ContractFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1)
    finding_type: str = Field(min_length=1)
    severity: Severity
    issue: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    recommendation: str = Field(min_length=1)
    evidence: str = Field(
        min_length=1,
        description="Exact supporting excerpt copied from the contract text",
    )
    suggested_revision: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class ContractFindings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[ContractFinding] = Field(default_factory=list)
    summary: str = Field(min_length=1)
    requires_legal_approval: bool
