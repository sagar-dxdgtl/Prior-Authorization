"""portal_captures.note: VARCHAR(2000) -> TEXT, so the audit row stops silently vanishing

`_record` is best-effort by design — a capture that reached the payer and produced a screenshot is
still a valid answer if the database is down, so a write failure is logged and swallowed rather than
raised. That is right for an outage. It is wrong for a value the schema can never accept, because
then the failure is not transient: the capture succeeds, the evidence row is dropped, and the log is
a record of the captures that happened to be SHORT rather than a record of what happened.

Observed live 2026-07-31 profiling Oscar (NPI 1568423168, oscar-fl-south-florida):

    portal_captures write failed for oscar-fl-south-florida/1568423168:
    (psycopg.errors.StringDataRightTruncation) value too long for type character varying(2000)

The walk completed and filed its screenshot; the row is simply absent from portal_captures. The notes
that overflow are the ones a reviewer most needs — Oscar's carries the network it pinned, the search
terms it tried, each result count, and why the plan could not be pinned. This is the same defect the
column has already had once at VARCHAR(400) (see tests/test_portal_store.py), fixed by widening to
2000; Oscar has now outgrown that too. A cap will keep being outgrown as drivers explain themselves
more fully, so the answer is not a bigger number.

`walk_trail` beside it is already TEXT. This brings `note` into line.

In Postgres VARCHAR(n) -> TEXT is binary-coercible, so this is a catalog update and does not rewrite
the table. Idempotent, matching 0025/0028/0036/0037/0038.

Revision ID: 0039_portal_capture_note_text
Revises: 0038_pnf_effective_dates
Create Date: 2026-07-31
"""

from __future__ import annotations

from alembic import op

revision = "0039_portal_capture_note_text"
down_revision = "0038_pnf_effective_dates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE portal_captures ALTER COLUMN note TYPE TEXT")


def downgrade() -> None:
    # Truncate before narrowing: any row written since the upgrade may exceed 2000 and would
    # otherwise fail the cast. Losing the tail of a note is the lesser evil in a rollback, and it is
    # the same information loss the old column imposed anyway.
    op.execute("UPDATE portal_captures SET note = left(note, 2000) WHERE length(note) > 2000")
    op.execute("ALTER TABLE portal_captures ALTER COLUMN note TYPE VARCHAR(2000)")
