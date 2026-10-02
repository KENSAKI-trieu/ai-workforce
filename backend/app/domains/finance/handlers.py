"""Imports every module that registers a finance approval handler.

Each kind of draft (journal entry, payment voucher, reminder) owns what approving it
does; they register themselves with app.domains.finance.approvals when imported.
"""

from app.domains.finance import journal_proposal, payments  # noqa: F401
