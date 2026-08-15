"""The capability broker — contribution C2.

The revocation test is the claim. If a dead token blocks unrelated later calls
of different kinds, the credential-layer argument holds; if it does not, the
defense is just call refusal with extra steps.
"""

from __future__ import annotations

import pytest

from sentinelz.broker.capability import (
    DENY_OUT_OF_SCOPE,
    DENY_RESTRICTED_TARGET,
    DENY_RESTRICTED_TOOL,
    DENY_REVOKED,
    DENY_UNKNOWN_TOKEN,
    CapabilityBroker,
    apply_scope_down,
)

SCOPES = {"send_email", "get_file_by_id", "share_file", "delete_file", "send_money", "search_emails"}


@pytest.fixture
def broker():
    b = CapabilityBroker()
    b.issue("s1", set(SCOPES))
    return b


# ----------------------------------------------------------------- issue


def test_issued_token_permits_in_scope_calls(broker):
    token = broker.token_for("s1")
    assert broker.check(token, "send_email", "bob@bluesparrowtech.com") is True


def test_out_of_scope_tool_is_refused(broker):
    token = broker.token_for("s1")
    assert broker.check(token, "update_password", "") is False
    assert broker.deny_reason(token, "update_password", "") == DENY_OUT_OF_SCOPE


def test_unknown_token_is_refused(broker):
    assert broker.check("not-a-token", "send_email", "") is False
    assert broker.deny_reason("not-a-token", "send_email", "") == DENY_UNKNOWN_TOKEN


def test_reissue_returns_the_same_token(broker):
    assert broker.issue("s1", SCOPES) == broker.token_for("s1")


def test_reissue_after_revocation_is_refused(broker):
    broker.revoke("s1")
    with pytest.raises(PermissionError):
        broker.issue("s1", SCOPES)


# ---------------------------------------------------------------- revoke


def test_revocation_kills_three_subsequent_calls_of_different_kinds(broker):
    """THE test. This is contribution C2.

    One decision revokes the session. Three later calls — a different egress
    tool, a read, and a destructive action, none of which triggered anything —
    all fail against the dead token. The agent cannot route around it, because
    the refusal happens in the broker rather than in the agent.
    """
    token = broker.token_for("s1")
    assert broker.check(token, "send_email", "mark.black-2134@gmail.com") is True

    broker.revoke("s1", reason="tainted call reaching beyond the task")

    assert broker.check(token, "share_file", "mark.black-2134@gmail.com") is False
    assert broker.check(token, "get_file_by_id", "6") is False
    assert broker.check(token, "delete_file", "13") is False

    reasons = {d["reason"] for d in broker.denials("s1")}
    assert reasons == {DENY_REVOKED}
    assert len(broker.denials("s1")) == 3
    assert broker.is_alive("s1") is False


def test_revocation_is_idempotent(broker):
    broker.revoke("s1")
    broker.revoke("s1")
    assert broker.is_alive("s1") is False


def test_revoking_an_unknown_session_is_harmless(broker):
    broker.revoke("does-not-exist")


def test_revocation_does_not_affect_another_session(broker):
    broker.issue("s2", SCOPES)
    broker.revoke("s1")
    assert broker.is_alive("s2") is True
    assert broker.check(broker.token_for("s2"), "send_email", "") is True


# ------------------------------------------------------------ scope_down


def test_scope_down_denies_a_named_tool(broker):
    broker.scope_down("s1", {"deny_tools": ["share_file"]})
    token = broker.token_for("s1")
    assert broker.check(token, "share_file", "x") is False
    assert broker.deny_reason(token, "share_file", "x") == DENY_RESTRICTED_TOOL
    assert broker.check(token, "send_email", "x") is True


def test_scope_down_denies_a_named_target(broker):
    broker.scope_down("s1", {"deny_targets": ["mark.black-2134@gmail.com"]})
    token = broker.token_for("s1")
    assert broker.check(token, "send_email", "to mark.black-2134@gmail.com") is False
    assert broker.deny_reason(token, "send_email", "mark.black-2134@gmail.com") == DENY_RESTRICTED_TARGET
    assert broker.check(token, "send_email", "bob@bluesparrowtech.com") is True


def test_restrictions_accumulate_and_never_widen(broker):
    broker.scope_down("s1", {"deny_tools": ["share_file"], "max_rows": 5})
    broker.scope_down("s1", {"deny_tools": ["delete_file"], "max_rows": 2})
    restrictions = broker.restrictions("s1")
    assert set(restrictions["deny_tools"]) == {"share_file", "delete_file"}
    assert restrictions["max_rows"] == 2

    broker.scope_down("s1", {"max_rows": 99})
    assert broker.restrictions("s1")["max_rows"] == 2


def test_scope_down_redacts_named_fields():
    scoped, note = apply_scope_down({"redact_fields": ["body"]}, {"recipients": ["a@b.com"], "body": "salary rows"})
    assert scoped["body"] == "[REDACTED]"
    assert scoped["recipients"] == ["a@b.com"]
    assert "redacted body" in note


def test_scope_down_caps_row_counts():
    payload = "\n".join(f"row {i}" for i in range(20))
    scoped, note = apply_scope_down({"max_rows": 3}, {"data": payload})
    assert scoped["data"].count("\n") <= 3
    assert "withheld" in scoped["data"]
    assert "capped" in note


def test_scope_down_caps_lists():
    scoped, note = apply_scope_down({"max_rows": 2}, {"recipients": ["a@x.com", "b@x.com", "c@x.com"]})
    assert len(scoped["recipients"]) == 2
    assert "capped" in note


def test_scope_down_with_no_restrictions_is_a_no_op():
    args = {"body": "hello"}
    scoped, note = apply_scope_down({}, args)
    assert scoped == args
    assert note == ""


def test_scope_down_does_not_mutate_the_input():
    args = {"body": "secret", "recipients": ["a@b.com"]}
    apply_scope_down({"redact_fields": ["body"]}, args)
    assert args["body"] == "secret"


# ------------------------------------------------------------------ misc


def test_reset_clears_everything(broker):
    broker.reset()
    assert broker.token_for("s1") is None
    assert broker.is_alive("s1") is False


def test_empty_scopes_permit_any_tool():
    """An empty scope set means "unscoped", not "nothing allowed" — that is
    how the undefended baseline runs through the same code path."""
    b = CapabilityBroker()
    token = b.issue("s", set())
    assert b.check(token, "anything_at_all", "") is True
