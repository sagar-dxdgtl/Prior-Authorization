from network_probe.domain import eligibility as elig
from network_probe.domain.benefits import EligibilityResult
from network_probe.domain.models import NetworkStatus, NetworkVerdict, ProviderQuery


class FakeCat:
    def __init__(self, pid):
        self._pid = pid

    def resolve(self, key):
        class P:
            pass

        P.stedi_payer_id = self._pid
        return P() if self._pid is not None else None


class FakeStedi:
    def __init__(self, result):
        self.result = result

    def check(self, q):
        return self.result


def _res(status):
    return EligibilityResult(
        coverage_active=True,
        plan_name=None,
        group=None,
        coverage_dates={},
        network_status=status,
        benefits=[],
        pcp_required=None,
        prior_auth_required=None,
        referral_required=None,
        cob=None,
        network_verdict=None,
        corroboration=[],
        source_audit={},
    )


def _verdict(status):
    return NetworkVerdict(
        status=status,
        matched_provider=None,
        plan_or_network_checked="x",
        source_url="u",
        confidence="high",
        notes="n",
        corroboration=[],
    )


def test_provider_verdict_in_wins_over_stedi_oon(monkeypatch):
    # the provider-network verdict is the authority; the 271's status never forces REVIEW
    monkeypatch.setattr(elig, "check_network", lambda q, **k: _verdict(NetworkStatus.IN_NETWORK))
    r = elig.check_eligibility(
        ProviderQuery(payer="oscar", plan_hint=""),
        catalogue=FakeCat("OSCAR"),
        stedi=FakeStedi(_res(NetworkStatus.OUT_OF_NETWORK)),
    )
    assert r.network_status == NetworkStatus.IN_NETWORK and r.network_verdict is not None


def test_provider_verdict_oon_wins_over_stedi_in(monkeypatch):
    # Perry/Munar fix: credentialing/TiC "OON" wins over the 271's unreliable "IN" indicator
    monkeypatch.setattr(elig, "check_network", lambda q, **k: _verdict(NetworkStatus.OUT_OF_NETWORK))
    r = elig.check_eligibility(
        ProviderQuery(payer="oscar", plan_hint=""),
        catalogue=FakeCat("OSCAR"),
        stedi=FakeStedi(_res(NetworkStatus.IN_NETWORK)),
    )
    assert r.network_status == NetworkStatus.OUT_OF_NETWORK


def test_stedi_unknown_adopts_directory(monkeypatch):
    monkeypatch.setattr(elig, "check_network", lambda q, **k: _verdict(NetworkStatus.IN_NETWORK))
    r = elig.check_eligibility(
        ProviderQuery(payer="oscar", plan_hint=""),
        catalogue=FakeCat("OSCAR"),
        stedi=FakeStedi(_res(NetworkStatus.UNKNOWN)),
    )
    assert r.network_status == NetworkStatus.IN_NETWORK


def test_no_directory_adapter_keeps_stedi(monkeypatch):
    def boom(q, **k):
        raise ValueError("No adapter")

    monkeypatch.setattr(elig, "check_network", boom)
    r = elig.check_eligibility(
        ProviderQuery(payer="mystery", plan_hint=""),
        catalogue=FakeCat(None),
        stedi=FakeStedi(_res(NetworkStatus.OUT_OF_NETWORK)),
    )
    assert r.network_status == NetworkStatus.OUT_OF_NETWORK and r.network_verdict is None


def test_base_url_threaded_into_check_network(monkeypatch):
    captured = {}

    def cn(q, **k):
        captured.update(k)
        return _verdict(NetworkStatus.UNKNOWN)

    monkeypatch.setattr(elig, "check_network", cn)
    elig.check_eligibility(
        ProviderQuery(payer="fhir", plan_hint=""),
        base_url="https://fhir.example/api",
        catalogue=FakeCat(None),
        stedi=FakeStedi(_res(NetworkStatus.UNKNOWN)),
    )
    assert captured.get("base_url") == "https://fhir.example/api"


def test_blank_plan_scoped_from_271_and_captures_pre_merge(monkeypatch):
    captured = {}

    def cn(q, **k):
        captured["plan_hint"] = q.plan_hint
        return _verdict(NetworkStatus.UNKNOWN)

    monkeypatch.setattr(elig, "check_network", cn)
    result = _res(NetworkStatus.UNKNOWN)
    result.selected_plan = "DEVOTED GIVEBACK 006 TX (HMO)"
    result.plan_candidates = [{"plan": "DEVOTED GIVEBACK 006 TX (HMO)", "is_product": True, "rank": 0}]
    out = elig.check_eligibility(
        ProviderQuery(payer="devoted", plan_hint="", npi="1720209885"),
        catalogue=FakeCat("DEVOT"),
        stedi=FakeStedi(result),
    )
    # the directory leg was scoped by the plan the 271 returned, not a blank hint
    assert captured["plan_hint"] == "DEVOTED GIVEBACK 006 TX (HMO)"
    # the 271-only status is captured before the merge overwrites network_status
    assert out.stedi_network_status == NetworkStatus.UNKNOWN


def test_explicit_plan_not_overridden(monkeypatch):
    captured = {}

    def cn(q, **k):
        captured["plan_hint"] = q.plan_hint
        return _verdict(NetworkStatus.UNKNOWN)

    monkeypatch.setattr(elig, "check_network", cn)
    result = _res(NetworkStatus.UNKNOWN)
    result.selected_plan = "DERIVED PLAN"
    elig.check_eligibility(
        ProviderQuery(payer="oscar", plan_hint="USER TYPED PLAN"),
        catalogue=FakeCat("OSCAR"),
        stedi=FakeStedi(result),
    )
    assert captured["plan_hint"] == "USER TYPED PLAN"


def test_stedi_payer_id_bypasses_catalogue(monkeypatch):
    seen = {}

    class FakeClient:
        def __init__(self, payer_id=None):
            seen["payer_id"] = payer_id

        def check(self, q):
            return _res(NetworkStatus.UNKNOWN)

    monkeypatch.setattr(elig, "StediEligibilityClient", FakeClient)
    monkeypatch.setattr(elig, "check_network", lambda q, **k: None)
    elig.check_eligibility(
        ProviderQuery(payer="stedi:128KY", plan_hint=""),
        catalogue=FakeCat(None),
        stedi_payer_id="128KY",
    )
    assert seen["payer_id"] == "128KY"


def test_recheck_network_reconciles_new_plan(monkeypatch):
    monkeypatch.setattr(elig, "check_network", lambda q, **k: _verdict(NetworkStatus.OUT_OF_NETWORK))
    out = elig.recheck_network(
        ProviderQuery(payer="oscar", plan_hint="OTHER PLAN", npi="1679766943"),
        NetworkStatus.IN_NETWORK,
        catalogue=FakeCat("OSCAR"),
    )
    # provider-network verdict OON wins over the 271's IN; no 270 was run
    assert out["network_status"] == "OUT_OF_NETWORK"
    assert out["network_verdict"]["status"] == "OUT_OF_NETWORK"


def test_recheck_network_no_adapter_keeps_stedi_status(monkeypatch):
    def boom(q, **k):
        raise ValueError("No adapter")

    monkeypatch.setattr(elig, "check_network", boom)
    out = elig.recheck_network(
        ProviderQuery(payer="mystery", plan_hint="X"),
        NetworkStatus.OUT_OF_NETWORK,
        catalogue=FakeCat(None),
    )
    assert out["network_status"] == "OUT_OF_NETWORK" and out["network_verdict"] is None
