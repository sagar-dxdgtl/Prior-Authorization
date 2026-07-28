"""Medicare **assignment** — the fact the no-network branch actually needs.

`no_network_verdict` settles Original Medicare / Medigap members, and its docstring has always
said the right rule: *"a provider who accepts Medicare assignment is in-network by definition"*.
What it checked was PECOS **enrollment**, which is a strictly weaker fact. Enrolled providers come
in three flavours and they do not cost the member the same thing:

  * **participating** (`ind_assgn = Y`) — charges the Medicare-approved amount. Care Compare prints
    exactly that sentence, and it is the proof saved for the Roulhac row.
  * **non-participating** (`ind_assgn = M`) — may balance-bill up to the 115% limiting charge. Only
    Medigap Plan F/G cover those Part B excess charges; on any other letter the member pays them.
  * **opted out** — a private contract. Medicare pays nothing at all, and neither does the
    supplement. That is genuinely out-of-network however you label it, and it is invisible to both
    PECOS and the assignment flag: opt-out lives in its own CMS file.

So enrollment alone cannot separate the member who owes $0 from the member who owes everything.
These tests pin the three-way split and the failure modes around it.

Live shapes verified 2026-07-28 against data.cms.gov:
  * National Downloadable File `mj5m-pzi6` — 3,387,942 rows; `ind_assgn` is **Y or M only**
    (Y: 3,131,543 / M: 256,399 / N: 0 — there is no "N", so absence of Y must not be read as "no").
  * Opt Out Affidavits — resolved by JSON:API like PECOS; carries an effective/end date pair.
"""

from network_probe.domain.enrollment import (
    ACCEPTS,
    MAY_EXCEED,
    OPTED_OUT,
    UNKNOWN_ASSIGNMENT,
    assignment_status,
)

_DAC_ROW = {
    "npi": "1801837109",
    "provider_last_name": "ROULHAC",
    "provider_first_name": "MAURICE",
    "pri_spec": "VASCULAR SURGERY",
    "state": "CO",
    "ind_assgn": "Y",
    "grp_assgn": "Y",
}

_OPTOUT_HEADERS = [
    "First Name", "Last Name", "NPI", "Specialty",
    "Optout Effective Date", "Optout End Date", "State Code", "Last updated",
]


class _Client:
    """Routes by URL: the JSON:API metadata call, the opt-out data-viewer, the DAC datastore."""

    def __init__(self, dac_rows=(), optout_rows=(), optout_boom=False, dac_boom=False):
        self.dac_rows, self.optout_rows = list(dac_rows), list(optout_rows)
        self.optout_boom, self.dac_boom = optout_boom, dac_boom
        self.urls = []

    def get_json(self, url, headers=None):
        self.urls.append(url)
        if "jsonapi" in url:
            return {"data": [{"id": "OPTOUT-LATEST", "attributes": {"field_dataset_version": "2026-06-01"}}]}
        if "data-viewer" in url:
            if self.optout_boom:
                raise RuntimeError("opt-out endpoint down")
            return {
                "meta": {"headers": _OPTOUT_HEADERS, "data_file_name": "OptOut_June2026.csv"},
                "data": self.optout_rows,
            }
        if self.dac_boom:
            raise RuntimeError("DAC endpoint down")
        return {"count": len(self.dac_rows), "results": self.dac_rows}


def _optout(npi, effective="01/30/1998", end="01/30/2028"):
    return [["Jonathan", "Raines", npi, "Psychiatry", effective, end, "PA", "02/16/2026"]]


# ---- the three-way split ---------------------------------------------------------------------

def test_participating_provider_accepts_assignment():
    """`ind_assgn = Y` IS the Care Compare sentence 'Charges the Medicare-approved amount'."""
    r = assignment_status("1801837109", client=_Client(dac_rows=[_DAC_ROW]))
    assert r.status == ACCEPTS
    assert "Medicare-approved amount" in r.detail


def test_non_participating_provider_may_exceed_the_approved_amount():
    """`M` is not a softer yes — it is the limiting-charge case the member may have to pay."""
    row = dict(_DAC_ROW, ind_assgn="M")
    r = assignment_status("1801837109", client=_Client(dac_rows=[row]))
    assert r.status == MAY_EXCEED
    assert "excess" in r.detail.lower()


def test_opted_out_provider_beats_a_yes_on_the_assignment_flag():
    """Opt-out is checked first and wins outright: under a private contract Medicare pays nothing,
    so a stale `ind_assgn = Y` must never be allowed to produce an in-network answer."""
    c = _Client(dac_rows=[_DAC_ROW], optout_rows=_optout("1801837109"))
    r = assignment_status("1801837109", client=c, today="2026-07-28")
    assert r.status == OPTED_OUT
    assert "private contract" in r.detail.lower()


# ---- opt-out is a date RANGE, not a presence check ---------------------------------------------

def test_an_expired_opt_out_is_not_a_current_opt_out():
    """The file retains historical affidavits. A provider whose opt-out ended in 2019 is billing
    Medicare again today; reading bare presence as opted-out would be a false OON."""
    c = _Client(dac_rows=[_DAC_ROW], optout_rows=_optout("1801837109", "01/30/2009", "01/30/2019"))
    r = assignment_status("1801837109", client=c, today="2026-07-28")
    assert r.status == ACCEPTS


def test_an_opt_out_that_has_not_started_yet_is_not_a_current_opt_out():
    c = _Client(dac_rows=[_DAC_ROW], optout_rows=_optout("1801837109", "01/30/2027", "01/30/2029"))
    r = assignment_status("1801837109", client=c, today="2026-07-28")
    assert r.status == ACCEPTS


def test_an_unparseable_opt_out_date_is_treated_as_current():
    """Conservative direction: we would rather withhold an IN than assert one on a row we can't read."""
    c = _Client(dac_rows=[_DAC_ROW], optout_rows=_optout("1801837109", "", ""))
    r = assignment_status("1801837109", client=c, today="2026-07-28")
    assert r.status == OPTED_OUT


def test_only_an_npi_exact_opt_out_row_counts():
    """The data-viewer is a keyword search — it returns near matches that are other people."""
    c = _Client(dac_rows=[_DAC_ROW], optout_rows=_optout("9999999999"))
    r = assignment_status("1801837109", client=c, today="2026-07-28")
    assert r.status == ACCEPTS


# ---- absence and failure must never over-claim --------------------------------------------------

def test_a_provider_absent_from_the_downloadable_file_is_unknown_not_a_no():
    """`ind_assgn` has no 'N' value, so 'not listed' cannot mean 'does not accept assignment'.
    The file only covers clinicians billing the Physician Fee Schedule."""
    r = assignment_status("1801837109", client=_Client(dac_rows=[]))
    assert r.status == UNKNOWN_ASSIGNMENT
    assert "not listed" in r.detail.lower()


def test_a_failed_assignment_lookup_is_unknown_not_a_no():
    r = assignment_status("1801837109", client=_Client(dac_boom=True))
    assert r.status == UNKNOWN_ASSIGNMENT


def test_a_failed_optout_lookup_is_recorded_so_the_verdict_can_discount_it():
    """We still answer from the assignment flag, but the caller must be able to see that the
    decisive negative source was never reached."""
    c = _Client(dac_rows=[_DAC_ROW], optout_boom=True)
    r = assignment_status("1801837109", client=c)
    assert r.status == ACCEPTS
    assert r.optout_checked is False


def test_a_successful_optout_lookup_is_recorded_as_checked():
    r = assignment_status("1801837109", client=_Client(dac_rows=[_DAC_ROW]))
    assert r.optout_checked is True


def test_a_malformed_npi_is_unknown_without_any_http_call():
    c = _Client(dac_rows=[_DAC_ROW])
    r = assignment_status("12345", client=c)
    assert r.status == UNKNOWN_ASSIGNMENT
    assert c.urls == []


# ---- multi-row NPIs -----------------------------------------------------------------------------

def test_disagreeing_rows_take_the_worse_answer():
    """One NPI has a row per practice location. If any of them says the clinician may exceed the
    approved amount, the member can be balance-billed — so 'M' beats 'Y', never the reverse."""
    rows = [dict(_DAC_ROW, ind_assgn="Y"), dict(_DAC_ROW, ind_assgn="M")]
    r = assignment_status("1801837109", client=_Client(dac_rows=rows))
    assert r.status == MAY_EXCEED


def test_rows_for_other_npis_are_ignored():
    rows = [dict(_DAC_ROW, npi="9999999999", ind_assgn="M"), _DAC_ROW]
    r = assignment_status("1801837109", client=_Client(dac_rows=rows))
    assert r.status == ACCEPTS
