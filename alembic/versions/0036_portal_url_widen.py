"""portal_captures.portal_url / walk_trail: widen to TEXT

Found by the clean Cigna run on 2026-07-28 (Orem / NPI 1497741409 / 34986). The capture succeeded,
read its answer from

  https://hcpdirectory.cigna.com/web/public/consumer/directory/doctors?...&medicalProductCode=OAP&medicalEcnCode=OA001

— **596 characters** — and then failed to persist with `value too long for type character
varying(400)`. `run_capture._record` is best-effort by design, so the row was dropped with only a
log line: the answer survived on screen, the evidence did not.

The perverse part is which captures this loses. `portal_url` is "the exact URL the answer was read
from", and for Cigna that URL is the proof — `medicalProductCode` / `medicalEcnCode` are what
confirm WHICH network answered, and they sit at the end of the longest URLs the system produces. So
the captures with the strongest corroboration were exactly the ones that could not be saved.

A URL has no natural length bound, and neither does an append-only walk trail, so both become TEXT
rather than a bigger guess. `note` is already 2000 and unchanged. In Postgres TEXT and VARCHAR share
a representation — widening is a catalogue change, not a table rewrite.

Idempotent + additive, matching 0025/0028.

Revision ID: 0036_portal_url_widen
Revises: 0035_wellcare_ga_atlanta
Create Date: 2026-07-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0036_portal_url_widen"
down_revision: str | None = "0035_wellcare_ga_atlanta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'portal_captures') THEN
                ALTER TABLE portal_captures ALTER COLUMN portal_url TYPE TEXT;
                ALTER TABLE portal_captures ALTER COLUMN walk_trail TYPE TEXT;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Deliberately not reverting: narrowing would truncate URLs already stored at full length.
    pass
