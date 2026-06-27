from __future__ import annotations
from dataclasses import dataclass
import uuid

@dataclass(frozen=True)
class RequestContext:
    tenant_id: uuid.UUID
    actor_id: uuid.UUID
    role: str
