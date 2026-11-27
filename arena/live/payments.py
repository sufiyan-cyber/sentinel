"""Payment providers behind one small interface.

`mock`      no network, deterministic order ids. The default, so the demo runs
            offline.
`cashfree`  creates a real order in Cashfree's SANDBOX. The order shows up in
            the Cashfree test dashboard, which is the on-stage proof that the
            payment leg is real.
`razorpay`  the same, through Razorpay's Orders API with test keys.

No money can move: the Razorpay provider refuses any key that is not a test
key, and creating an order does not capture a payment.
"""

from __future__ import annotations

import contextlib
import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from arena import config

TEST_KEY_PREFIX = "rzp_test_"


class PaymentError(RuntimeError):
    pass


@dataclass
class PaymentResult:
    provider: str
    mode: str  # "mock" | "test"
    order_id: str
    status: str
    amount_paise: int
    raw: dict[str, Any] = field(default_factory=dict)


class PaymentProvider(Protocol):
    name: str
    mode: str

    def create_order(self, amount_paise: int, receipt: str, notes: dict[str, str]) -> PaymentResult: ...


class MockProvider:
    name = "mock"
    mode = "mock"

    def create_order(self, amount_paise: int, receipt: str, notes: dict[str, str]) -> PaymentResult:
        digest = hashlib.sha256(f"{receipt}:{amount_paise}".encode()).hexdigest()[:14]
        return PaymentResult(self.name, self.mode, f"order_MOCK{digest}", "created", amount_paise)


class RazorpayTestProvider:
    name = "razorpay"
    mode = "test"

    def __init__(self, key_id: str | None = None, key_secret: str | None = None) -> None:
        self.key_id = (config.RAZORPAY_KEY_ID if key_id is None else key_id).strip()
        self.key_secret = (config.RAZORPAY_KEY_SECRET if key_secret is None else key_secret).strip()
        if not self.key_id or not self.key_secret:
            raise PaymentError("RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are not set")
        if not self.key_id.startswith(TEST_KEY_PREFIX):
            # Never weaken this. A live key here could create real orders.
            raise PaymentError("refusing a non-test Razorpay key: the key id must start with rzp_test_")

    def create_order(self, amount_paise: int, receipt: str, notes: dict[str, str]) -> PaymentResult:
        try:
            response = httpx.post(
                f"{config.RAZORPAY_BASE_URL.rstrip('/')}/orders",
                auth=(self.key_id, self.key_secret),
                json={"amount": amount_paise, "currency": "INR", "receipt": receipt[:40], "notes": notes},
                timeout=15.0,
            )
        except httpx.HTTPError as exc:
            raise PaymentError(f"Razorpay unreachable: {type(exc).__name__}") from exc
        if response.status_code not in (200, 201):
            raise PaymentError(f"Razorpay refused the order (HTTP {response.status_code})")
        data = response.json()
        return PaymentResult(
            self.name,
            self.mode,
            str(data.get("id", "")),
            str(data.get("status", "created")),
            int(data.get("amount", amount_paise)),
            raw={"receipt": data.get("receipt")},
        )


class CashfreeSandboxProvider:
    """Creates a real order in Cashfree's SANDBOX through the PG Orders API.

    Two guards keep this off production: the request only ever goes to the
    sandbox host, and a production secret key is refused outright.
    """

    name = "cashfree"
    mode = "test"

    def __init__(self, app_id: str | None = None, secret_key: str | None = None) -> None:
        self.app_id = (config.CASHFREE_APP_ID if app_id is None else app_id).strip()
        self.secret_key = (config.CASHFREE_SECRET_KEY if secret_key is None else secret_key).strip()
        self.base_url = config.CASHFREE_BASE_URL.rstrip("/")
        if not self.app_id or not self.secret_key:
            raise PaymentError("CASHFREE_APP_ID / CASHFREE_SECRET_KEY are not set")
        # Never weaken these. A production key or host could create real orders.
        if "prod" in self.secret_key.lower():
            raise PaymentError("refusing a production Cashfree key: use the Test-mode secret key")
        if "sandbox.cashfree.com" not in self.base_url:
            raise PaymentError("refusing a non-sandbox Cashfree URL: it must be sandbox.cashfree.com")

    def create_order(self, amount_paise: int, receipt: str, notes: dict[str, str]) -> PaymentResult:
        try:
            response = httpx.post(
                f"{self.base_url}/orders",
                headers={
                    "x-client-id": self.app_id,
                    "x-client-secret": self.secret_key,
                    "x-api-version": config.CASHFREE_API_VERSION,
                },
                json={
                    "order_amount": amount_paise / 100,
                    "order_currency": "INR",
                    "customer_details": {"customer_id": "sentinelz_demo", "customer_phone": "9999999999"},
                    "order_note": receipt[:100],
                    "order_tags": notes,
                },
                timeout=15.0,
            )
        except httpx.HTTPError as exc:
            raise PaymentError(f"Cashfree unreachable: {type(exc).__name__}") from exc
        if response.status_code not in (200, 201):
            detail = ""
            with contextlib.suppress(ValueError):
                detail = str(response.json().get("message", ""))[:160]
            raise PaymentError(f"Cashfree refused the order (HTTP {response.status_code}) {detail}".strip())
        data = response.json()
        return PaymentResult(
            self.name,
            self.mode,
            str(data.get("order_id") or data.get("cf_order_id") or ""),
            str(data.get("order_status", "ACTIVE")),
            amount_paise,
            raw={"cf_order_id": data.get("cf_order_id")},
        )


def build_provider(name: str | None = None) -> PaymentProvider:
    """The configured provider. A misconfigured gateway raises — it does not
    quietly become the mock, or a 'real' demo could be silently fake."""
    chosen = (name or config.PAYMENTS).strip().lower()
    if chosen == "cashfree":
        return CashfreeSandboxProvider()
    if chosen == "razorpay":
        return RazorpayTestProvider()
    return MockProvider()
