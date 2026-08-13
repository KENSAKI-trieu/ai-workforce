from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentRoutingDecision(BaseModel):
    """Validated LLM decision; the registry remains authoritative."""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, description="Registered agent role code")
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)

    @field_validator("role")
    @classmethod
    def normalize_role(cls, value: str) -> str:
        return value.strip().upper()
