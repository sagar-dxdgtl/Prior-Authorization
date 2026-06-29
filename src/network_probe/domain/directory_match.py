"""Match one of *our* providers against a parsed PDF directory (no NPIs on the directory side).

We hold the provider's name + state + ZIP accurately (intake), so we look them up in the stored
directory rows by surname, then disambiguate by first name and ZIP. Honest by construction:
a confirmed surname-not-in-the-member's-state is OUT_OF_NETWORK; an un-disambiguable common name
is UNKNOWN (never a guessed verdict). The NPI stays on our side and is attached by the adapter.
"""

from __future__ import annotations

import re

from network_probe.domain.models import NetworkStatus

_Result = tuple[NetworkStatus, dict | None, str, str]  # (status, matched_provider, confidence, note)


def _norm(s: str | None) -> str:
    return re.sub(r"[^A-Z]", "", (s or "").upper())


def _zip5(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")[:5]


def _get(row, attr: str):
    return row.get(attr) if isinstance(row, dict) else getattr(row, attr, None)


def _provider(row) -> dict:
    return {
        "name": _get(row, "full_name"),
        "specialty": _get(row, "specialty"),
        "city": _get(row, "city"),
        "state": _get(row, "state"),
        "zip": _get(row, "zip"),
        "accepting_new": _get(row, "accepting_new"),
    }


def match_directory(
    candidates,
    *,
    payer_label: str,
    last_name: str | None,
    first_name: str | None = None,
    state: str | None = None,
    zip_code: str | None = None,
) -> _Result:
    """Resolve a provider against `candidates` (directory rows already filtered to this payer)."""
    ln = _norm(last_name)
    if not ln:
        return (NetworkStatus.UNKNOWN, None, "low", "A provider name is required to match a PDF directory.")

    same = [c for c in candidates if _norm(_get(c, "last_name")) == ln]
    if not same:
        return (
            NetworkStatus.OUT_OF_NETWORK,
            None,
            "medium",
            f"No provider with surname {last_name!r} is listed in {payer_label}'s directory.",
        )

    st = (state or "").upper()[:2]
    work = same
    if st:
        in_state = [c for c in same if (_get(c, "state") or "").upper() == st]
        if not in_state:
            return (
                NetworkStatus.OUT_OF_NETWORK,
                None,
                "medium",
                f"{last_name} appears in {payer_label}'s directory but not in {st} — the member's plan "
                f"is {st}, so the provider is not in-network there.",
            )
        work = in_state

    fn = _norm(first_name)
    z = _zip5(zip_code)
    loc = f" in {st}" if st else ""

    def fmatch(c) -> bool:
        cf = _norm(_get(c, "first_name"))
        return bool(fn) and bool(cf) and (cf.startswith(fn[:3]) or fn.startswith(cf[:3]))

    def zmatch(c) -> bool:
        return bool(z) and _zip5(_get(c, "zip")) == z

    # strongest: first-name AND ZIP both match
    fz = [c for c in work if fmatch(c) and zmatch(c)]
    if fz:
        return (
            NetworkStatus.IN_NETWORK,
            _provider(fz[0]),
            "high",
            f"{_get(fz[0], 'full_name')} matches by name and ZIP {z}{loc} in {payer_label}'s directory.",
        )

    # surname + ZIP, uniquely
    zc = [c for c in work if zmatch(c)]
    if len(zc) == 1:
        return (
            NetworkStatus.IN_NETWORK,
            _provider(zc[0]),
            "high",
            f"{_get(zc[0], 'full_name')} matches by surname and ZIP {z}{loc} in {payer_label}'s directory.",
        )

    # first + last (+ state), uniquely
    fc = [c for c in work if fmatch(c)]
    if len(fc) == 1:
        return (
            NetworkStatus.IN_NETWORK,
            _provider(fc[0]),
            "medium",
            f"{_get(fc[0], 'full_name')} matches by first + last name{loc} in {payer_label}'s directory.",
        )
    if len(fc) > 1:
        return (
            NetworkStatus.UNKNOWN,
            None,
            "medium",
            f"{len(fc)} providers named {first_name} {last_name}{loc} in {payer_label}'s directory — "
            f"provide the ZIP to disambiguate.",
        )

    # a single same-surname provider in the member's state, no first/zip to confirm with
    if len(work) == 1 and not fn and not z:
        return (
            NetworkStatus.IN_NETWORK,
            _provider(work[0]),
            "medium",
            f"{_get(work[0], 'full_name')} is the only {last_name} listed{loc} in {payer_label}'s directory.",
        )

    # surname present, but can't confirm THIS provider → honest UNKNOWN (never a guessed OON)
    return (
        NetworkStatus.UNKNOWN,
        None,
        "low",
        f"{len(work)} providers with surname {last_name}{loc} in {payer_label}'s directory, none matching "
        f"first name {first_name!r} / ZIP {z or '?'} — can't confirm this specific provider.",
    )
