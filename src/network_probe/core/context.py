from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class RequestContext:
    tenant_id: uuid.UUID
    actor_id: uuid.UUID
    role: str
