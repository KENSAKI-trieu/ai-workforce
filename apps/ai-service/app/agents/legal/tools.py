"""Tools the Legal agent may be offered: the ceiling the backend's grant is
intersected with. Tools run in the backend; this only names them."""

# `audit_contract_risk` reaches the same deterministic review engine as the backend's
# Legal chat -- clause splitting, the per-contract-type checklist, perspective-aware
# severity -- and stores and escalates the review the same way.
TOOLS: tuple[str, ...] = (
    "rag_search",
    "audit_contract_risk",
    "generate_legal_document",
    "submit_approval_request",
)
