from pydantic import BaseModel, ConfigDict, Field, model_validator


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str | None = None
    document_title: str = Field(min_length=1)
    section_title: str | None = None
    page: int | None = Field(default=None, ge=1)
    chunk_id: str | None = None
    quote: str = Field(min_length=1, description="Exact supporting text from the supplied context")


class RAGAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)
    citations: list[Citation] = Field(default_factory=list)
    grounded: bool

    @model_validator(mode="after")
    def require_evidence_for_grounded_answer(self) -> "RAGAnswer":
        if self.grounded and not self.citations:
            raise ValueError("A grounded answer must include at least one citation")
        return self
