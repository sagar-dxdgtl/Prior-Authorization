def test_resolver_noop_without_key(monkeypatch):
    monkeypatch.delenv("STEDI_API_KEY", raising=False)
    monkeypatch.setattr("scripts.resolve_payer_ids.get_secret", lambda k: None)
    from scripts.resolve_payer_ids import resolve_all

    assert resolve_all() == 0


def test_search_payer_picks_id_on_name_match():
    import httpx

    from network_probe.core._http import CachedClient
    from scripts.resolve_payer_ids import search_payer

    def handler(req):
        return httpx.Response(
            200, json={"items": [{"displayName": "Some Payer", "primaryPayerId": "12345", "stediId": "ABCDE"}]}
        )

    client = CachedClient(cache_dir=None, delay_seconds=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    # name matches displayName -> returns primaryPayerId (the tradingPartnerServiceId)
    assert search_payer(client, "k", "Some Payer") == "12345"


def test_search_payer_no_match_returns_none():
    import httpx

    from network_probe.core._http import CachedClient
    from scripts.resolve_payer_ids import search_payer

    def handler(req):
        return httpx.Response(200, json={"items": [{"displayName": "Totally Different Co", "primaryPayerId": "999"}]})

    client = CachedClient(cache_dir=None, delay_seconds=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert search_payer(client, "k", "Some Payer") is None
