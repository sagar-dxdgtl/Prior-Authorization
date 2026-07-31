"""A TiC fact must carry the date its MRF was BUILT, not only the date we wrote the row.

Ins Test 3 row 5, 2026-07-31. TiC says Raikar (NPI 1841308830, TIN 843012976) is IN the BCBS IL PPO
Participating roster; the portal, read the same day, says OUT — decisive, plan confirmed by
productCode=ILIL1M, complete set. Read as a straight contradiction that is a REVIEW.

It is not necessarily a contradiction. The fact's own label says `BCBS IL PPO Participating Provider
network (2026-06-14)` — the MRF was built 47 days before the portal was read, so a contract that
ended in between makes TiC stale rather than wrong. That is the same mechanism that resolved row 3 in
the opposite direction: Oscar's TiC was stale because the contract STARTED after its MRF was built.

Telling stale from wrong needs the build date in a comparable column. It was sitting in free text:
`source_built_at` was NULL on all 120 facts. Migration 0038 added the column; nothing filled it.

`retrieved_at` cannot stand in — that is when WE wrote the row (2026-07-28), which says nothing about
how old the payer's file already was.
"""

import datetime as dt

from network_probe.domain.tic_facts import source_built_at_from_label


def test_reads_the_build_date_out_of_the_label():
    """The real labels in provider_network_facts today."""
    cases = {
        "BCBS IL PPO Participating Provider network (2026-06-14)": dt.date(2026, 6, 14),
        "Cigna national Open Access Plus (OAP) in-network MRF, 2026-07-01": dt.date(2026, 7, 1),
        "Aetna ALICFI exchange, file pl-2ef-hr23 (2026-07-05) — bounded 2-of-283 sample":
            dt.date(2026, 7, 5),
    }
    for label, expected in cases.items():
        assert source_built_at_from_label(label) == expected, label


def test_returns_none_when_the_label_carries_no_date():
    """NULL means 'this source is undated', which must never be read as 'built today'. An undated
    source cannot be shown to be stale, so the reconciler has to treat it as a genuine conflict."""
    for label in (None, "", "BCBS IL PPO Participating Provider network", "no date here"):
        assert source_built_at_from_label(label) is None, label


def test_ignores_a_number_that_is_not_a_date():
    """`pl-2ef-hr23` and `2-of-283` sit in the same label as a real date."""
    assert source_built_at_from_label("file pl-2ef-hr23 — bounded 2-of-283 sample") is None


def test_rejects_an_impossible_date_rather_than_guessing():
    assert source_built_at_from_label("network (2026-13-45)") is None


def test_facts_from_csv_stamp_the_build_date(tmp_path):
    """The loader must stamp it, or every future pull repeats the row-5 ambiguity."""
    from network_probe.domain.tic_facts import facts_from_crosswalk_csv

    csv_path = tmp_path / "x.csv"
    csv_path.write_text("npi,tin,payer\n1841308830,843012976,bcbsil\n")
    rows = facts_from_crosswalk_csv(
        csv_path, "bcbs-anthem-il",
        network_name="BCBS IL PPO Participating Provider network (2026-06-14)",
    )
    assert rows[0]["source_built_at"] == dt.date(2026, 6, 14)


def test_an_undated_label_leaves_the_column_unset(tmp_path):
    """Absent, not a guessed default — see test_returns_none_when_the_label_carries_no_date."""
    from network_probe.domain.tic_facts import facts_from_crosswalk_csv

    csv_path = tmp_path / "x.csv"
    csv_path.write_text("npi,tin,payer\n1841308830,843012976,bcbsil\n")
    rows = facts_from_crosswalk_csv(csv_path, "bcbs-anthem-il", network_name="Some roster")
    assert "source_built_at" not in rows[0] or rows[0]["source_built_at"] is None
