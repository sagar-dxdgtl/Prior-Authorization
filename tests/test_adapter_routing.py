from __future__ import annotations

import pytest

from network_probe.domain import service as svc
from network_probe.payers.adapters.oscar import OscarAdapter


class _StubCat:
    """Catalogue that resolves no row -> forces the fhir_base_url path to be skipped."""

    def resolve(self, payer):
        return None


def test_oscar_roster_key_routes_to_oscar_adapter():
    # Roster keys look like 'oscar-fl-south-florida'; the bespoke Oscar adapter is registered only
    # under the short key 'oscar', so resolving a payer by its roster key used to raise
    # 'No adapter for payer' and broke every Oscar case selected from the UI. The first key segment
    # must fall back to the short-key adapter.
    adapter = svc.get_adapter("oscar-fl-south-florida", catalogue=_StubCat())
    assert isinstance(adapter, OscarAdapter)


def test_unknown_payer_key_still_raises():
    with pytest.raises(ValueError):
        svc.get_adapter("totally-unknown-payer", catalogue=_StubCat())
