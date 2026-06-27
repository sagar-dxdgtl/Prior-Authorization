"""network_probe — per-payer provider network-status verification.

Given a provider (NPI / name) and a payer + plan, query the payer's public
"Find a Doctor" directory and return a structured IN / OUT / UNKNOWN verdict.

See DISCOVERY.md for the reverse-engineered endpoint contract this is built on.
"""

from network_probe.base import PayerAdapter
from network_probe.models import NetworkStatus, NetworkVerdict, ProviderQuery
from network_probe.service import check_network, get_adapter

__all__ = [
    "NetworkStatus",
    "ProviderQuery",
    "NetworkVerdict",
    "PayerAdapter",
    "check_network",
    "get_adapter",
]
