"""Choosing WHICH in-network file to read out of a BlueCard control-plan index.

THE PROBLEM THIS SOLVES. A Blue plan's own in-network MRF lists only the providers it contracted
with in its OWN state. Measured 2026-08-12, BCBS SC's "PREFERRED BLUE" file (126 MB gzipped, 1.5 GB
raw) resolves to 8,534 distinct (npi, tin) pairs and 12 of 12 sampled against NPPES are in South
Carolina. So when a South Carolina member is treated in Atlanta, their provider is not in it — and
must not be: an out-of-state member is served through **BlueCard**, where the HOST plan (the Blue in
the state where care happens) supplies the network and the rate. All four BCBS SC rows on the
8-12-26 sheet are treated in GA and FL, their NPIs appear zero times in BCBS SC's own file, and the
payer's own portal attests one of them IS in-network. Reading that absence as out-of-network would
be a confident wrong answer produced by opening the wrong file.

Control-plan indexes carry the host plans' files, which is what makes this fixable: BCBS SC's index
holds 15,998 entries — 169 distinct files across 49 issuers, of which only 24 are its own.

WHY NOT `pull_tic_index.select_files(state=...)`. That matches the state needle as a substring of
the LOCATION URL as well as the description. Correct for a Cigna-style index whose URLs encode the
state; catastrophic here, because BlueCard URLs are signed and "ga"/"fl"/"sc" occur at random inside
base64 signatures. Measured on the live index:

    --state SC  ->  15,872 of 15,998 entries      (99.2% — a filter that filters nothing)
    --state GA  ->   5,773 entries, headed by CareFirst BCBS, BCBS Alabama, BCBS Tennessee,
                     BCBS Minnesota and BCBS Wyoming, with no Georgia file in the top six

So this matches on the ISSUER only, and never on the URL.

TWO THINGS IT DELIBERATELY WILL NOT DO.

1. It will not pick ONE network for you. Georgia offers BlueChoice PPO, Blue Open Access POS and PAR
   providers; which one a given member reaches is a plan-level fact the index does not carry. Every
   candidate is returned, for the caller to sweep or a human to choose.
2. It will not resolve an issuer that names no single state. `Anthem BCBS` carries 19 networks here
   and Anthem operates in roughly fourteen states; the index never says which. Such an issuer is
   never auto-selected — `host_plan_files` declines and says what it saw. Declining costs a lookup;
   guessing costs a wrong network answer, which is the failure this layer exists to prevent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_STATE_NAMES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "PR": "Puerto Rico", "VI": "Virgin Islands",
}

# Blues whose NAME does not contain their state, mapped only where the territory is unambiguous.
# Anything not listed and not self-naming simply does not match — see the module docstring.
_ISSUER_STATES: dict[str, frozenset[str]] = {
    "empire bcbs": frozenset({"NY"}),
    "excellus bcbs": frozenset({"NY"}),
    "carefirst bcbs": frozenset({"MD", "DC", "VA"}),
    "independence bc": frozenset({"PA"}),
    "capital bc": frozenset({"PA"}),
    "highmark bs": frozenset({"PA"}),
    "premera bc": frozenset({"WA", "AK"}),
    "regence blueshield": frozenset({"WA"}),
    "triple-s management corporation": frozenset({"PR"}),
    "bcbs u.s.v.i.": frozenset({"VI"}),
}

# Issuers that span several states without saying which. NEVER auto-selected.
# "Anthem Blue Cross" is California's brand and "Anthem Central" a regional grouping, but the index
# distinguishes none of them by state, so all three decline rather than risk the wrong host plan.
_AMBIGUOUS = ("anthem", "bcbs kansas city")


@dataclass(frozen=True)
class IndexFile:
    """One in-network file the index offers, with its issuer and network split out."""

    description: str
    issuer: str
    network: str
    location: str  # the SIGNED url as handed out; these expire, so fetch the one you were given


def parse_description(description: str) -> tuple[str, str]:
    """`"BCBS Georgia - BlueChoice PPO"` -> `("BCBS Georgia", "BlueChoice PPO")`.

    Split on the FIRST separator only: network names contain " - " of their own, and the issuer is
    the part that decides which state's contract this is.
    """
    issuer, sep, network = (description or "").partition(" - ")
    return issuer.strip(), (network.strip() if sep else "")


def index_files(data: dict) -> list[IndexFile]:
    """Flatten a TiC index to its DISTINCT in-network files.

    De-duplicated on (description, url-without-query). The live index repeats every file across its
    71 reporting structures — 15,998 entries for 169 real files — and each repeat carries a different
    signature, so de-duplicating on the raw URL would not collapse them and the caller would download
    the same file scores of times.
    """
    out: list[IndexFile] = []
    seen: set[tuple[str, str]] = set()
    for rs in data.get("reporting_structure") or []:
        for f in rs.get("in_network_files") or []:
            desc = (f.get("description") or "").strip()
            loc = f.get("location") or ""
            parts = urlsplit(loc)
            key = (desc, f"{parts.scheme}://{parts.netloc}{parts.path}")
            if key in seen:
                continue
            seen.add(key)
            issuer, network = parse_description(desc)
            out.append(IndexFile(description=desc, issuer=issuer, network=network, location=loc))
    return out


def by_network(files: list[IndexFile]) -> dict[str, list[IndexFile]]:
    """Group files by network name, because A NETWORK IS USUALLY SEVERAL FILES.

    The host plans shard: measured live, BCBS Georgia's BlueChoice PPO is `1_of_2` and `2_of_2`, and
    BCBS Wyoming's Wyoming Select runs to `13_of_13`. Ingesting one shard and reading the result as
    the network's full roster would manufacture an absence, so a caller must take every file in the
    group or none of them.
    """
    out: dict[str, list[IndexFile]] = {}
    for f in files:
        out.setdefault(f.network, []).append(f)
    return out


def _ambiguous(issuer: str) -> bool:
    low = issuer.lower()
    return any(low.startswith(a) for a in _AMBIGUOUS)


def issuer_serves(issuer: str, state: str) -> bool:
    """Whether `issuer` is the Blue plan for `state`. Conservative by design."""
    st = (state or "").strip().upper()
    if st not in _STATE_NAMES or not issuer:
        return False
    if _ambiguous(issuer):
        return False
    curated = _ISSUER_STATES.get(issuer.strip().lower())
    if curated is not None:
        return st in curated
    # The state's FULL NAME, as whole words. Full names rather than abbreviations because
    # "North Carolina" must not match "BCBS South Carolina", and it does not.
    if re.search(rf"\b{re.escape(_STATE_NAMES[st])}\b", issuer, re.I):
        return True
    # A standalone uppercase abbreviation, as in "Highmark BCBS WV". Case-sensitive on purpose:
    # a lowercase match would read "IN" out of "Independence" and hand Indiana a Pennsylvania plan.
    return re.search(rf"\b{st}\b", issuer) is not None


def host_plan_files(files: list[IndexFile], state: str) -> tuple[list[IndexFile], str]:
    """Every file belonging to the Blue plan that serves `state`, and why.

    Returns ``([], reason)`` rather than a guess when no issuer names the state — the reason lists
    what the index did offer, and calls out any multi-state issuer that was skipped, so an operator
    can choose one deliberately instead of the code choosing one silently.
    """
    st = (state or "").strip().upper()
    if st not in _STATE_NAMES:
        return [], f"{state!r} is not a US state or territory code; nothing selected."

    picked = [f for f in files if issuer_serves(f.issuer, st)]
    if picked:
        names = sorted({f.issuer for f in picked})
        nets = by_network(picked)
        shards = ", ".join(
            f"{n} ({len(v)} file{'s' if len(v) > 1 else ''})" for n, v in sorted(nets.items())
        )
        return picked, (
            f"{_STATE_NAMES[st]} host plan: {', '.join(names)} — "
            f"{len(nets)} network(s) in {len(picked)} file(s): {shards}"
        )

    why = f"no issuer in this index names {_STATE_NAMES[st]}."
    skipped = sorted({f.issuer for f in files if _ambiguous(f.issuer)})
    if skipped:
        why += (f" {', '.join(skipped)} span several states and the index does not say which, so "
                f"they are not selected automatically — name one explicitly if it is the right host.")
    present = sorted({f.issuer for f in files})
    why += f" Issuers present ({len(present)}): {', '.join(present[:20])}"
    if len(present) > 20:
        why += ", …"
    return [], why
