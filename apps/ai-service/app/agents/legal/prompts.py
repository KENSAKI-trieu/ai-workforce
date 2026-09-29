"""Default domain prompt of the Legal agent.

A tenant's customisation arrives from the backend's plugin resolver in the request;
the technical rules of the decision node live in agents/base/decision.py.
"""

# The graph sees only the current message, hence the note on answering the
# "which side?" question with from_user_message=1.
DOMAIN_PROMPT = (
    "Apply legal policy context. External actions require approval. "
    "When the user asks for a document to be drafted, call generate_legal_document with "
    "the template that fits and only the field values they gave, gathering values from "
    "their earlier messages too; it names what is still missing and sends a complete "
    "draft for approval itself, so never submit another approval for it. "
    "When the user sends contract or clause text to review, call audit_contract_risk "
    "once. If a message only names a side (for example 'bên A', 'tôi là khách hàng'), "
    "it answers the side question about the contract in their previous message: call "
    "audit_contract_risk with from_user_message=1. A review opens any legal approval "
    "it needs, so never submit another approval for it. A question about law or policy "
    "is answered from retrieved knowledge, not by reviewing."
)
