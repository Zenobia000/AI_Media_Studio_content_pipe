"""A provider's spent budget, which is not the failure of whatever asked (providers/receipts.ts)."""

from __future__ import annotations


def budget_message(service: str, window: str) -> str:
    """The text of a spent budget's error; with `%` for both parts it is the LIKE pattern that finds every saved one."""
    return f"Budget for {service} exhausted ({window})"


class BudgetExceededError(Exception):
    def __init__(self, service: str, window: str, retry_after_seconds: int) -> None:
        super().__init__(budget_message(service, window))
        self.service = service
        self.retry_after_seconds = retry_after_seconds
