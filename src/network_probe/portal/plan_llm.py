"""Tier 3 of plan matching: ask a model which network label the member's plan names.

`plan_match` resolves a 271's plan string onto a portal's own network labels in two deterministic
tiers — an identifier match (contract / HIOS / market code), then distinctive-token overlap. When
both decline there is nothing left to try, and the driver can only return UNKNOWN. That is the
single reason six of the eleven Ins Test 3 rows are unsettled: the plan string names a *payer*
where the portal wants a *network*.

This is the only place in the system a model is asked anything, and it is deliberately the
narrowest possible job — pick one of N given labels, or decline. Two properties make that safe:

**An LLM pick can never license an OUT_OF_NETWORK.** It returns confidence "medium" at best, so
`PlanMatch.confirms_network` stays False. The pick is good enough to *search* a network; a provider
absent from it still reads UNKNOWN. Absence only becomes OON behind an identifier-grade pin, which
this can never produce. That boundary is why a model can be in this path at all: a wrong answer
costs a wasted search, not a wrong verdict.

**No member data leaves the process.** HANDOFF §7 — tier 3 is scrubbed to plan descriptors. This
client's "Ins Group Number" column holds member identifiers (an alpha-prefixed `SRG########` and a
12-digit numeric), and the plan string is built from that column, so it is scrubbed here before the
request is built rather than trusted upstream.

Every failure — a decline, a hallucinated index, a refusal, a dead connection — returns None, which
degrades to the same UNKNOWN the caller would have had anyway.
"""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from network_probe.portal.plan_match import PlanMatch

log = logging.getLogger("preauth.plan_llm")

#: Pinned deliberately. Do not swap models without re-scoring the drivers against Test2 — the
#: whole point of this layer is that its behaviour is bounded and checkable.
MODEL = "claude-opus-5"

#: Member-identifier shapes from this client's workbook (HANDOFF §0 / §7). The alpha-prefixed form
#: is 3 letters + 8 digits; the other is a bare 9+ digit run. Contract/PBP ids (H1036, H2406018000)
#: are ONE letter + 4 digits and are deliberately not matched — they are the strongest signal the
#: model has, so scrubbing them would defeat the call.
_MEMBER_ALPHA = re.compile(r"\b[A-Za-z]{3}\d{6,}\b")
_MEMBER_NUMERIC = re.compile(r"\b\d{9,}\b")
_REDACTED = "[redacted]"

_SYSTEM = (
    "You map a health-plan name onto a payer's own network labels.\n"
    "You are given the plan as the payer's eligibility response described it, and the exact list "
    "of network labels that payer's provider directory offers.\n"
    "Return the INDEX of the one label the plan names. Return null if the plan does not clearly "
    "name exactly one of them — including when it names only the payer, only a product type such "
    "as 'PPO' that several labels share, or when two labels fit equally well.\n"
    "Declining is the correct answer whenever there is doubt: a wrong pick sends a provider search "
    "to the wrong network, whereas null simply leaves the question open."
)


class _Choice(BaseModel):
    index: int | None = Field(description="Index of the matching label, or null if none clearly matches.")
    reason: str = Field(description="One short sentence naming what in the plan string decided it.")


#: An API key that has found its way into an exception message. Logged output from this module is
#: read off demo machines and pasted into chats, so diagnosability must not leak a credential.
_API_KEY_SHAPE = re.compile(r"sk-ant-[A-Za-z0-9_\-]+")


def scrub_descriptor(text: str | None) -> str:
    """Strip member identifiers from a plan string, keeping the plan descriptors."""
    if not text:
        return ""
    return _MEMBER_NUMERIC.sub(_REDACTED, _MEMBER_ALPHA.sub(_REDACTED, text))


def _scrub_error(exc: Exception) -> str:
    return _API_KEY_SHAPE.sub(_REDACTED, str(exc))


def _settings_api_key() -> str | None:
    try:
        from network_probe.core.config import get_settings

        return get_settings().anthropic_api_key
    except Exception:  # noqa: BLE001 — no settings is not an error here, just no key
        return None


def _build_client():
    """An Anthropic client, or None with a log line that says which problem it is.

    The key is resolved HERE rather than left to the SDK. `anthropic.Anthropic()` reads only
    `os.environ`, so a key in `.env` — where every other secret in this project lives, and where
    `Settings` reads it from — was invisible to it. The client then constructed successfully and
    raised a bare `TypeError` at request time, which landed in the request handler and reported
    itself as a malformed call. Tier 3 failed 100% of the time and said the wrong thing about why.
    """
    import os

    key = os.environ.get("ANTHROPIC_API_KEY") or _settings_api_key()
    if not key:
        log.info(
            "tier-3 plan disambiguation unavailable: no ANTHROPIC_API_KEY in the environment or .env"
        )
        return None
    try:
        import anthropic

        return anthropic.Anthropic(api_key=key)
    except Exception as exc:  # noqa: BLE001 — package missing/broken: fall back to UNKNOWN
        log.info("tier-3 plan disambiguation unavailable (%s: %s)", type(exc).__name__, _scrub_error(exc))
        return None


def disambiguate_plan(wanted: str | None, options: list[str], *, client=None) -> PlanMatch | None:
    """Ask the model which of `options` the plan `wanted` names. None when it cannot say.

    `options` is the portal's own label list, in its order — the returned `index` addresses it.
    """
    if not wanted or len(options) < 2:
        return None  # nothing to choose between; never spend a call

    plan = scrub_descriptor(wanted)
    labels = "\n".join(f"{i}. {scrub_descriptor(o)}" for i, o in enumerate(options))

    if client is None:
        client = _build_client()
        if client is None:
            return None

    try:
        res = client.messages.parse(
            model=MODEL,
            max_tokens=1024,
            output_config={"effort": "low"},
            system=_SYSTEM,
            messages=[{"role": "user", "content": f"Plan: {plan}\n\nNetwork labels:\n{labels}"}],
            output_format=_Choice,
        )
    except Exception as exc:  # noqa: BLE001 — a model outage must never fail a capture
        # The message, not just the type: every distinct failure printed the same shape before, and
        # that is what left a 100%-failing tier 3 sitting unexplained.
        log.warning("tier-3 plan disambiguation failed: %s: %s", type(exc).__name__, _scrub_error(exc))
        return None

    # A safety decline returns HTTP 200 with stop_reason "refusal" and no usable content.
    if getattr(res, "stop_reason", None) == "refusal":
        return None

    choice = getattr(res, "parsed_output", None)
    idx = getattr(choice, "index", None)
    if idx is None or not isinstance(idx, int) or not (0 <= idx < len(options)):
        return None  # declined, or hallucinated an index that is not a real option

    return PlanMatch(
        index=idx,
        label=options[idx],
        # NEVER "high". confirms_network is identifier-only, so this pins a plan for the search
        # and cannot license an OUT_OF_NETWORK. See the module docstring.
        confidence="medium",
        basis=(
            f"tier 3: no identifier and no unique token match, so a model was asked to choose "
            f"among the {len(options)} labels the portal offers — it chose {options[idx]!r} "
            f"({getattr(choice, 'reason', '') or 'no reason given'}). A name-derived match pins "
            f"the plan for searching but cannot license an out-of-network verdict."
        ),
    )
