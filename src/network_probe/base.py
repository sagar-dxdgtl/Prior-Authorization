"""The single interface every payer adapter implements.

Keep this payer-agnostic. Anything Oscar-specific belongs in adapters/oscar.py.
"""

from abc import ABC, abstractmethod

from network_probe.domain.models import NetworkVerdict, ProviderQuery


class PayerAdapter(ABC):
    #: lowercase payer key used to select this adapter (e.g. "oscar")
    payer_name: str

    @abstractmethod
    def check_network(self, q: ProviderQuery) -> NetworkVerdict:
        """Return a NetworkVerdict for the given provider + plan query."""
        ...
