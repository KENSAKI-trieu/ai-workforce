"""Finance agent: answers from the company's own books through fixed-parameter tools.

It always runs on the graph. Every figure in a reply must come from a tool result of the
turn (DomainPolicy.numbers_from_tools); what it drafts goes to a person for approval.
"""
