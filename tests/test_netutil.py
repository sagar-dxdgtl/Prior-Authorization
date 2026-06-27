import pytest

from network_probe.api.netutil import assert_safe_url


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/", "http://127.0.0.1/", "http://10.0.0.5/fhir",
    "http://192.168.1.1/", "http://0.0.0.0/", "http://[::1]/", "file:///etc/passwd", "ftp://x/y",
])
def test_blocks_internal_and_nonhttp(url):
    with pytest.raises(ValueError):
        assert_safe_url(url)

def test_blocks_hostname_resolving_internal(monkeypatch):
    monkeypatch.setattr("network_probe.api.netutil.socket.getaddrinfo",
                        lambda host, *a, **k: [(2, 1, 6, "", ("127.0.0.1", 0))])
    with pytest.raises(ValueError):
        assert_safe_url("http://localhost.evil.test/")

def test_allows_public_literal_ip():
    assert assert_safe_url("https://1.1.1.1/api") == "https://1.1.1.1/api"

def test_allows_public_hostname(monkeypatch):
    monkeypatch.setattr("network_probe.api.netutil.socket.getaddrinfo",
                        lambda host, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
    assert assert_safe_url("https://example.test/") == "https://example.test/"
