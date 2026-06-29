"""Parse a payer's monthly PDF provider directory into structured rows.

Some plans (e.g. Align Senior Care, a FL HMO I-SNP run on AllyAlign) publish their network
ONLY as a giant monthly PDF — no FHIR API, and crucially **no NPIs**, just name + address.
Because our use is client-based, we never resolve the whole directory to NPIs. We parse the
rows (name + location + specialty) into the DB once a month, then match *our own* providers
(whose name + city/state/zip we already hold from intake) against it by name + state, keeping
the NPI on our side. See domain/directory_match.py for the lookup.

Record shape in the PDF (anchored reliably by the "Available As Of:" line):

    JOHN SCHMIDT                  <- provider name (the line immediately before the anchor)
    Available As Of: 1/1/2021
    Accepting New Patients: Yes
    2441 SURFSIDE BLVD STE 200    <- one or more (street / city,ST,ZIP / Phone) location blocks
    CAPE CORAL, FL, 33914
    Phone: (239) 541-7500

Parsing is done with PyMuPDF (fitz) — fast enough for the ~10k-page files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_ANCHOR = "Available As Of:"
_CSZ = re.compile(r"^(.+?),\s*([A-Z]{2}),\s*(\d{5})(?:-\d{4})?$")  # CAPE CORAL, FL, 33914
_FOOTER = re.compile(r"^H\d{4}_.*ProviderDirectory.*\s*\d*$")  # AllyAlign page footer
_PAGE_FOOTER = re.compile(r"^\d+\s*\|\s*P\s*a\s*g\s*e\s*$", re.I)  # AaNeel "41 | P a g e" footer
_ACCEPTING = re.compile(r"Accepting New Patients:\s*(\w+)", re.I)
_AVAIL = re.compile(r"Available As Of:\s*([\d/]+)", re.I)
_PHONE = re.compile(r"Phone:")
# AaNeel/eternalHealth: internal provider id like "P0191519-258948" anchors each record
_PROVIDER_ID = re.compile(r"^[A-Z]\d{4,}-\d{4,}$")
_GENDER_SUFFIX = re.compile(r"\((?:M|F)\)\s*$")


@dataclass
class DirectoryEntry:
    name: str
    last_name: str
    first_name: str
    specialty: str | None = None
    available_as_of: str | None = None
    accepting_new: bool | None = None
    locations: list[dict] = field(default_factory=list)  # [{address, city, state, zip}]

    @property
    def states(self) -> set[str]:
        return {loc["state"] for loc in self.locations if loc.get("state")}

    @property
    def zips(self) -> set[str]:
        return {loc["zip"] for loc in self.locations if loc.get("zip")}


def _is_name_line(line: str) -> bool:
    """A provider-name line: letters (with initials/apostrophes/hyphens), not an address,
    field label, footer, or city/state/zip line."""
    s = line.strip()
    if not s or any(ch.isdigit() for ch in s):
        return False
    if ":" in s or _CSZ.match(s) or _FOOTER.match(s):
        return False
    # mostly A-Z letters + spaces/.'- ; reject section headers in parens like "CARDIOLOGY (261)"
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z .'\-]+", s)) and "(" not in s


def _split_name(full: str) -> tuple[str, str]:
    """Best-effort (last, first) from an uppercase 'FIRST [MIDDLE] LAST' name."""
    parts = full.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[-1], parts[0]  # last token = surname, first token = given


def parse_directory_pdf(
    path: str, fmt: str = "allyalign", specialties: set[str] | None = None
) -> list[DirectoryEntry]:
    """Parse the directory PDF at `path` into DirectoryEntry rows.

    `fmt`: "allyalign" (Align Senior Care — anchored on 'Available As Of:') or "aaneel"
    (eternalHealth — anchored on the internal provider id). `specialties`: optional TOC
    specialty headers (allyalign only); matching never needs specialty.
    """
    import fitz  # PyMuPDF

    lines: list[str] = []
    with fitz.open(path) as doc:
        for page in doc:
            for raw in page.get_text().splitlines():
                s = raw.strip()
                if s and not _FOOTER.match(s) and not _PAGE_FOOTER.match(s):
                    lines.append(s)
    return parse_lines_aaneel(lines) if fmt == "aaneel" else parse_lines(lines, specialties)


def parse_lines(lines: list[str], specialties: set[str] | None = None) -> list[DirectoryEntry]:
    """The pure record-extraction core (no PDF dependency) — unit-testable with raw lines."""
    entries: list[DirectoryEntry] = []
    cur_specialty: str | None = None
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        # track specialty section headers (exact match against the known set)
        if specialties and line.upper() in specialties:
            cur_specialty = line.title()
            i += 1
            continue
        if line == _ANCHOR or line.startswith(_ANCHOR):
            # name is the immediately-preceding name-ish line
            name = lines[i - 1] if i > 0 and _is_name_line(lines[i - 1]) else None
            if not name:
                i += 1
                continue
            last, first = _split_name(name)
            e = DirectoryEntry(name=name, last_name=last, first_name=first, specialty=cur_specialty)
            m = _AVAIL.search(line)
            if m:
                e.available_as_of = m.group(1)
            # walk the location block until the next provider name (line before next anchor)
            j = i + 1
            pending_addr: str | None = None
            while j < n:
                lj = lines[j]
                if lj.startswith(_ANCHOR):  # next record begins one line back
                    break
                if _is_name_line(lj) and j + 1 < n and lines[j + 1].startswith(_ANCHOR):
                    break  # lj is the next provider's name
                am = _ACCEPTING.search(lj)
                cm = _CSZ.match(lj)
                if am:
                    e.accepting_new = am.group(1).lower().startswith("y")
                elif cm:
                    e.locations.append(
                        {
                            "address": pending_addr,
                            "city": cm.group(1).strip(),
                            "state": cm.group(2),
                            "zip": cm.group(3),
                        }
                    )
                    pending_addr = None
                elif _PHONE.search(lj):
                    pending_addr = None
                elif not _is_name_line(lj):  # a street address line
                    pending_addr = lj
                j += 1
            entries.append(e)
            i = j
            continue
        i += 1
    return entries


def parse_lines_aaneel(lines: list[str]) -> list[DirectoryEntry]:
    """AaNeel/eternalHealth layout, anchored on the internal provider id:

        NAME[(M|F)] / Specialty / P0191519-258948 / Org[/ Org line 2] / Address / CITY, ST, ZIP / phone
    """
    entries: list[DirectoryEntry] = []
    n = len(lines)
    for i in range(2, n):
        if not _PROVIDER_ID.match(lines[i]):
            continue
        name = _GENDER_SUFFIX.sub("", lines[i - 2]).strip()
        specialty = lines[i - 1].strip()
        if not name or ":" in name or _CSZ.match(name) or _PROVIDER_ID.match(name):
            continue
        # the next CITY, ST, ZIP within a few lines closes the record; the line above it is the street
        k = next((j for j in range(i + 1, min(i + 9, n)) if _CSZ.match(lines[j])), None)
        if k is None:
            continue
        cm = _CSZ.match(lines[k])
        last, first = _split_name(name)
        e = DirectoryEntry(name=name, last_name=last, first_name=first, specialty=specialty or None)
        e.locations.append(
            {
                "address": lines[k - 1] if k - 1 > i else None,
                "city": cm.group(1).strip(),
                "state": cm.group(2),
                "zip": cm.group(3),
            }
        )
        entries.append(e)
    return entries


def toc_specialties(path: str, max_pages: int = 8) -> set[str]:
    """Collect specialty section names from the table of contents (e.g. 'CARDIOLOGY (261)')."""
    import fitz

    specs: set[str] = set()
    with fitz.open(path) as doc:
        for k in range(min(max_pages, doc.page_count)):
            for line in doc[k].get_text().splitlines():
                m = re.match(r"^([A-Z][A-Z &'/]+)\s*\(\d+\)\s*$", line.strip())
                if m:
                    specs.add(m.group(1).strip())
    return specs
