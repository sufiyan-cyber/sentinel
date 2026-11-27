"""The agent's wallet. Integer paise, an append-only ledger, never negative."""

from __future__ import annotations

import threading
from typing import Any

from arena import config


class InsufficientFundsError(RuntimeError):
    pass


class Wallet:
    def __init__(self, opening_inr: int | None = None) -> None:
        self.opening_paise = (config.WALLET_INR if opening_inr is None else opening_inr) * 100
        self.balance_paise = self.opening_paise
        self.ledger: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def debit(self, amount_paise: int, ref: str, title: str = "") -> int:
        """Take `amount_paise` out. Returns the new balance."""
        if amount_paise <= 0:
            raise ValueError("debit amount must be positive")
        with self._lock:
            if amount_paise > self.balance_paise:
                raise InsufficientFundsError(
                    f"wallet has {self.balance_paise} paise, needs {amount_paise}"
                )
            self.balance_paise -= amount_paise
            self.ledger.append(
                {"ref": ref, "amount_paise": -amount_paise, "title": title, "seq": len(self.ledger) + 1}
            )
            return self.balance_paise

    def credit(self, amount_paise: int, ref: str, title: str = "Wallet top-up") -> int:
        """Add `amount_paise`. Returns the new balance."""
        if amount_paise <= 0:
            raise ValueError("credit amount must be positive")
        with self._lock:
            self.balance_paise += amount_paise
            self.ledger.append(
                {"ref": ref, "amount_paise": amount_paise, "title": title, "seq": len(self.ledger) + 1}
            )
            return self.balance_paise

    def snapshot(self, delta_paise: int = 0, reason: str = "") -> dict[str, Any]:
        return {
            "balance_paise": self.balance_paise,
            "opening_paise": self.opening_paise,
            "delta_paise": delta_paise,
            "reason": reason,
            "ledger": list(self.ledger[-10:]),
        }

    def reset(self) -> None:
        with self._lock:
            self.balance_paise = self.opening_paise
            self.ledger.clear()


def inr(paise: int) -> str:
    """₹1,299 / ₹499.50 — for tool results and chat text."""
    rupees, rest = divmod(abs(int(paise)), 100)
    text = f"₹{rupees:,}" + (f".{rest:02d}" if rest else "")
    return f"-{text}" if paise < 0 else text
