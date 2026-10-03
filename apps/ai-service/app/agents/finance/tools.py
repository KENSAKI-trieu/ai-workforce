"""Tools the Finance agent may be offered: the ceiling the backend's grant is
intersected with. Tools run in the backend; this only names them."""

# Every figure the agent states has to come from one of these: they read the company's
# own books, imported or posted, with fixed parameters -- there is no free-form query.
# Tools that write (journal entries, payment vouchers, reminders) only open an approval.
TOOLS: tuple[str, ...] = (
    "rag_search",
    "lookup_invoices",
    "propose_journal_entry",
    "get_account_balance",
    "get_trial_balance",
    "get_ledger_detail",
    "budget_vs_actual",
    "ar_ap_aging",
    "payment_schedule",
    "get_account_trend",
    "get_expense_breakdown",
    "list_spreadsheets",
    "analyze_spreadsheet",
    "draft_payment_voucher",
    "draft_payment_reminder",
)
