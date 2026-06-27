from network_probe.secrets_provider import EnvSecrets, get_secret

def test_env_secret(monkeypatch):
    monkeypatch.setenv("FOO_KEY", "bar")
    assert EnvSecrets().get_secret("FOO_KEY") == "bar"
    assert EnvSecrets().get_secret("MISSING") is None

def test_get_secret_prefers_env_when_no_aws(monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.setenv("STEDI_API_KEY", "live-key")
    assert get_secret("STEDI_API_KEY") == "live-key"
