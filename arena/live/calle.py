"""CALL-E Agentic Phone Calling integration (https://www.heycall-e.com/).

Allows the live assistant to dispatch real agentic phone calls via the CALL-E
Developer API (`/v2/calls` with fallback to `/v1/calls`) when the user asks to
call a phone number or contact (e.g., "call my friend and tell them...").
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

import httpx

from arena import config

logger = logging.getLogger(__name__)

_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
_DIGITS_IN_TEXT_RE = re.compile(r"(?:\+?\d[\d\s\-]{7,15}\d)")


def normalize_e164_phone(raw: str | None, fallback_phone: str = "") -> str:
    """Normalize a phone string into canonical E.164 format (+country_code...).

    If `raw` is a contact label like 'my friend' or empty, falls back to
    `fallback_phone` (`CALLE_DEFAULT_PHONE`).
    """
    candidate = (raw or "").strip()
    digits_only = re.sub(r"\D", "", candidate)

    # If candidate has fewer than 8 digits (e.g. "friend", "my friend", ""), try fallback
    if len(digits_only) < 8:
        candidate = (fallback_phone or "").strip()
        digits_only = re.sub(r"\D", "", candidate)

    if not digits_only or len(digits_only) < 8:
        return ""

    if candidate.startswith("+") and _E164_RE.match(f"+{digits_only}"):
        return f"+{digits_only}"

    # 10-digit Indian mobile number -> +91XXXXXXXXXX
    if len(digits_only) == 10 and digits_only[0] in "6789":
        return f"+91{digits_only}"

    # 11-digit starting with 0 -> strip leading 0 and prefix +91
    if len(digits_only) == 11 and digits_only.startswith("0") and digits_only[1] in "6789":
        return f"+91{digits_only[1:]}"

    # 12-digit starting with 91 -> +91...
    if len(digits_only) == 12 and digits_only.startswith("91"):
        return f"+{digits_only}"

    # 10-digit US/Canada number -> +1...
    if len(digits_only) == 10:
        return f"+1{digits_only}"

    return f"+{digits_only}"


def extract_phone_from_text(text: str) -> str:
    """Extract a phone number embedded in natural-language text if present."""
    match = _DIGITS_IN_TEXT_RE.search(text)
    if not match:
        return ""
    return normalize_e164_phone(match.group(0))


class CalleService:
    """Client for the CALL-E Agentic Phone Calling API (`https://api.heycall-e.com`)."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_phone: str | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else config.CALLE_API_KEY).strip()
        self.base_url = (base_url or config.CALLE_BASE_URL).rstrip("/")
        self.default_phone = (
            default_phone if default_phone is not None else config.CALLE_DEFAULT_PHONE
        ).strip()
        self.latest_call: dict[str, Any] | None = None

    def _refresh_env_if_needed(self) -> None:
        try:
            from dotenv import dotenv_values

            vals = dotenv_values(config.REPO_ROOT / ".env")
            if not self.api_key:
                env_key = (vals.get("CALLE_API_KEY") or vals.get("CALL_E_API_KEY") or "").strip()
                if env_key:
                    self.api_key = env_key
            if not self.default_phone:
                env_phone = (
                    vals.get("CALLE_DEFAULT_PHONE") or vals.get("CALL_E_DEFAULT_PHONE") or ""
                ).strip()
                if env_phone:
                    self.default_phone = env_phone
        except Exception:
            pass

    @property
    def configured(self) -> bool:
        self._refresh_env_if_needed()
        return bool(self.api_key)

    def set_config(self, api_key: str | None = None, default_phone: str | None = None) -> None:
        if api_key is not None and api_key.strip():
            self.api_key = api_key.strip()
        if default_phone is not None and default_phone.strip():
            self.default_phone = default_phone.strip()

    def _headers(self, idempotency_key: str) -> dict[str, str]:
        self._refresh_env_if_needed()
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Idempotency-Key": idempotency_key,
            "Content-Type": "application/json",
        }

    def place_call(
        self,
        phone: str,
        message: str,
        recipient_name: str = "friend",
    ) -> dict[str, Any]:
        """Dispatch an outbound agentic phone call via CALL-E."""
        self._refresh_env_if_needed()
        self.latest_call = None

        if not self.configured:
            return {
                "ok": False,
                "dispatched": False,
                "error": (
                    "CALLE_API_KEY is not configured. Add CALLE_API_KEY to .env or paste it in "
                    "Settings (⚙️) under CALL-E Phone Calling."
                ),
            }

        target_phone = normalize_e164_phone(phone, fallback_phone=self.default_phone)
        if not target_phone:
            target_phone = extract_phone_from_text(message)
        if not target_phone:
            return {
                "ok": False,
                "dispatched": False,
                "error": (
                    "No recipient phone number was provided and CALLE_DEFAULT_PHONE is not set. "
                    "Please specify a phone number (e.g. +919876543210) or set a default friend "
                    "number in Settings (⚙️) / .env (CALLE_DEFAULT_PHONE)."
                ),
            }

        clean_recipient = (recipient_name or "the recipient").strip()
        clean_message = (message or "Hello, checking in from Aura assistant.").strip()
        call_task = (
            f"Call {clean_recipient} and deliver this message clearly and politely: "
            f'"{clean_message}". Confirm that they heard the message and note any brief reply.'
        )

        idem_key = f"sentinelz-call-{uuid.uuid4().hex}"

        # Dry run is the default: a demo must never ring a real phone by
        # accident. Same record, same events, no HTTP request.
        if config.CALLE_DRY_RUN:
            record = {
                "ok": True,
                "dispatched": True,
                "dry_run": True,
                "call_id": f"dryrun-{idem_key[-8:]}",
                "status": "simulated",
                "phone": target_phone,
                "recipient": clean_recipient,
                "message": clean_message,
                "task": call_task,
            }
            self.latest_call = record
            return record

        v2_body: dict[str, Any] = {
            "phone": target_phone,
            "task": call_task,
            "result_schema": {
                "type": "object",
                "properties": {
                    "message_delivered": {
                        "type": "string",
                        "enum": ["yes", "no", "unknown"],
                        "description": "Use yes if the message was delivered to the recipient.",
                    },
                    "recipient_reply": {
                        "type": "string",
                        "description": "Short summary of what the recipient replied.",
                    },
                },
                "required": ["message_delivered"],
                "additionalProperties": False,
            },
        }

        last_err = ""
        # 1. Try CALL-E v2 Agentic Calls endpoint (/v2/calls)
        try:
            resp = httpx.post(
                f"{self.base_url}/v2/calls",
                headers=self._headers(idem_key),
                json=v2_body,
                timeout=25.0,
            )
            if resp.status_code in (200, 201, 202):
                data = resp.json()
                call_id = str(data.get("id") or data.get("call_id") or idem_key)
                status = str(data.get("status") or "queued")
                record = {
                    "ok": True,
                    "dispatched": True,
                    "call_id": call_id,
                    "status": status,
                    "phone": target_phone,
                    "recipient": clean_recipient,
                    "message": clean_message,
                    "task": call_task,
                }
                self.latest_call = record
                return record
            last_err = f"HTTP {resp.status_code}: {resp.text[:300]}"
        except Exception as exc:
            last_err = str(exc)

        # 2. Fallback to CALL-E v1 Calls endpoint (/v1/calls)
        v1_idem = f"sentinelz-v1-{uuid.uuid4().hex}"
        v1_body: dict[str, Any] = {
            "task": f"Call {target_phone} ({clean_recipient}) and tell them: {clean_message}",
            "recipients": [{"phones": [target_phone]}],
        }
        try:
            resp_v1 = httpx.post(
                f"{self.base_url}/v1/calls",
                headers=self._headers(v1_idem),
                json=v1_body,
                timeout=25.0,
            )
            if resp_v1.status_code in (200, 201, 202):
                data_v1 = resp_v1.json()
                call_id = str(data_v1.get("id") or v1_idem)
                status = str(data_v1.get("status") or "queued")
                record = {
                    "ok": True,
                    "dispatched": True,
                    "call_id": call_id,
                    "status": status,
                    "phone": target_phone,
                    "recipient": clean_recipient,
                    "message": clean_message,
                    "task": call_task,
                }
                self.latest_call = record
                return record
            last_err = f"HTTP {resp_v1.status_code}: {resp_v1.text[:300]}"
        except Exception as exc:
            last_err = str(exc)

        return {
            "ok": False,
            "dispatched": False,
            "phone": target_phone,
            "error": f"CALL-E API error dispatching call to {target_phone}: {last_err}",
        }


CALLE_SERVICE = CalleService()
