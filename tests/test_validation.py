import pytest

from network_probe.api.validation import normalize_dob, valid_npi


def test_npi_luhn():
    assert valid_npi("1679766943") is True  # real NPI (Dr Herron) — Luhn-valid
    assert valid_npi("1234567890") is False
    assert valid_npi("abc") is False
    assert valid_npi("123") is False
    assert valid_npi("") is False


def test_normalize_dob():
    assert normalize_dob("01/02/1980") == "1980-01-02"
    assert normalize_dob("1980-01-02") == "1980-01-02"
    with pytest.raises(ValueError):
        normalize_dob("not-a-date")
