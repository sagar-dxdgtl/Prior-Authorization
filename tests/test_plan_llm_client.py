"""How tier 3 gets its credential, and what it says when it cannot.

Tier 3 failed **100% of the time** and reported it as `tier-3 plan disambiguation failed: TypeError`
— which reads like a bad request and is why it sat unexplained. Neither half of that was true:

  * The key is configured. It is in `.env`, where every other secret in this project lives and where
    `Settings` reads it from. But `plan_llm` called a bare `anthropic.Anthropic()`, and the SDK only
    consults `os.environ` — so in any process that had not exported the variable (which is all of
    them: nothing here exports it) the client **constructed fine** and only failed at request time.
  * The SDK raises a plain `TypeError` for unresolvable auth, so the failure landed in the
    *request* handler rather than the *unavailable* one, and the log line named the wrong problem.

Logging `type(exc).__name__` alone is what hid it: every distinct failure — no key, no network, a
malformed request — printed the same shape. The message is now logged too, with anything
key-shaped redacted, since this path exists to be diagnosable from a demo machine.
"""

import logging
import sys
import types

import pytest

from network_probe.portal import plan_llm


@pytest.fixture(autouse=True)
def _no_ambient_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def fake_sdk(monkeypatch):
    """Stand in for the `anthropic` package and record how the client was constructed."""
    seen = {}

    class _Anthropic:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    mod = types.ModuleType("anthropic")
    mod.Anthropic = _Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return seen


def _settings_key(monkeypatch, key):
    monkeypatch.setattr(
        plan_llm, "_settings_api_key", lambda: key, raising=False
    )


# ---- where the key comes from --------------------------------------------------------------------

def test_the_key_is_read_from_settings_so_a_dot_env_key_is_actually_used(monkeypatch, fake_sdk):
    """The whole bug: the key was in .env and the SDK never looked there."""
    _settings_key(monkeypatch, "sk-ant-from-dot-env")
    assert plan_llm._build_client() is not None
    assert fake_sdk["api_key"] == "sk-ant-from-dot-env"


def test_an_exported_environment_variable_still_wins(monkeypatch, fake_sdk):
    """Overriding the key for one run must not require editing .env."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    _settings_key(monkeypatch, "sk-ant-from-dot-env")
    plan_llm._build_client()
    assert fake_sdk["api_key"] == "sk-ant-from-env"


def test_no_key_anywhere_declines_without_constructing_a_client(monkeypatch, fake_sdk):
    _settings_key(monkeypatch, None)
    assert plan_llm._build_client() is None
    assert fake_sdk == {}


def test_the_no_key_message_names_the_variable_and_says_where_it_looked(monkeypatch, caplog):
    _settings_key(monkeypatch, None)
    with caplog.at_level(logging.INFO, logger="preauth.plan_llm"):
        plan_llm._build_client()
    msg = caplog.text
    assert "ANTHROPIC_API_KEY" in msg
    assert ".env" in msg


# ---- diagnosability ------------------------------------------------------------------------------

def test_a_request_failure_logs_the_message_not_just_the_exception_type(monkeypatch, caplog):
    """`TypeError` on its own was indistinguishable from a malformed request."""

    class _Boom:
        class messages:
            @staticmethod
            def parse(**kwargs):
                raise TypeError("Could not resolve authentication method.")

    with caplog.at_level(logging.WARNING, logger="preauth.plan_llm"):
        out = plan_llm.disambiguate_plan("Some Plan", ["A", "B"], client=_Boom())
    assert out is None
    assert "Could not resolve authentication method" in caplog.text


def test_a_key_appearing_in_an_error_message_is_redacted(monkeypatch, caplog):
    """Diagnosability must not turn into a credential in a log file or a shared terminal."""

    class _Boom:
        class messages:
            @staticmethod
            def parse(**kwargs):
                raise RuntimeError("bad key sk-ant-api03-SECRETVALUE123 rejected")

    with caplog.at_level(logging.WARNING, logger="preauth.plan_llm"):
        plan_llm.disambiguate_plan("Some Plan", ["A", "B"], client=_Boom())
    assert "SECRETVALUE123" not in caplog.text
    assert "[redacted]" in caplog.text


# ---- the caller still degrades to UNKNOWN ---------------------------------------------------------

def test_disambiguate_returns_none_when_no_client_can_be_built(monkeypatch):
    _settings_key(monkeypatch, None)
    assert plan_llm.disambiguate_plan("BCBSAZ Statewide PPO", ["Statewide / National PPO", "Alliance HMO"]) is None


def test_a_client_is_never_built_when_there_is_nothing_to_choose_between(monkeypatch, fake_sdk):
    """Guard the existing early return: one option is not a choice, and must not spend a call."""
    _settings_key(monkeypatch, "sk-ant-key")
    assert plan_llm.disambiguate_plan("Anything", ["Only One"]) is None
    assert fake_sdk == {}
