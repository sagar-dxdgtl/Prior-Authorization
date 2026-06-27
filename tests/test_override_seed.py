"""The seeded golden-record override corrects Rodriguez (Devoted CO PPO · Dr Li) to OON."""
from pathlib import Path

from network_probe.domain.corroboration import finalize
from network_probe.domain.models import NetworkStatus, NetworkVerdict, ProviderQuery
from network_probe.domain.overrides import OverrideStore

SEED = Path(".overrides/overrides.json")


def _devoted_in_verdict():
    return NetworkVerdict(status=NetworkStatus.IN_NETWORK,
                          matched_provider={"npi": "1629339312", "name": "Jing Li, MD"},
                          plan_or_network_checked="devoted CO PPO", source_url="http://dir",
                          confidence="high", notes="listed in directory.")


def test_seed_file_exists_and_has_rodriguez():
    assert SEED.exists(), "seed override file missing"
    store = OverrideStore(path=SEED)
    q = ProviderQuery(payer="devoted", plan_hint="PPO", npi="1629339312", last_name="Li")
    assert store.lookup(q) is not None


def test_seed_override_flips_rodriguez_to_oon():
    store = OverrideStore(path=SEED)
    q = ProviderQuery(payer="devoted", plan_hint="PPO", npi="1629339312", last_name="Li")
    out = finalize(_devoted_in_verdict(), q, override_store=store)
    assert out.status == NetworkStatus.OUT_OF_NETWORK and out.confidence == "high"
    assert "availity" in out.notes.lower()
