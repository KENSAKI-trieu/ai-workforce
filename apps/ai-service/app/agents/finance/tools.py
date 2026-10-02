"""Tools the Finance agent may be offered: the ceiling the backend's grant is
intersected with. Tools run in the backend; this only names them."""

# Every figure the agent states has to come from one of these: they read the company's
# own books, imported or posted, with fixed parameters -- there is no free-form query.
# Tools that write (journal entries, payment vouchers, reminders) only open an approval.
TOOLS: tuple[str, ...] = (
    "rag_search",
    "lookup_invoices",
)
