from __future__ import annotations
import logging
from typing import Optional
from .crypto import FernetCrypto, hash_member_id
from .config import get_settings
from .db.session import tenant_session
from .db.repo import EligibilityCheckRepo
from .context import RequestContext
from .benefits import EligibilityResult
from .models import ProviderQuery

log = logging.getLogger("preauth.audit")

def _crypto() -> FernetCrypto:
    return FernetCrypto(get_settings().fernet_key_list)

def _full_name(q: ProviderQuery) -> Optional[str]:
    name = " ".join(p for p in (q.first_name, q.last_name) if p).strip()
    return name or None

def write_audit(ctx: RequestContext, action: str, q: ProviderQuery,
                result: EligibilityResult, request_id: str) -> None:
    s = get_settings()
    name = _full_name(q)
    has_phi = bool(q.member_id or q.dob or name)
    crypto = _crypto() if has_phi else None
    mid_hash = hash_member_id(q.member_id, s.member_id_pepper) if q.member_id else None
    with tenant_session(ctx.tenant_id) as sess:
        EligibilityCheckRepo(sess, ctx.tenant_id).record(
            actor_id=ctx.actor_id, action=action, payer_key=q.payer,
            member_id_hash=mid_hash,
            member_id_enc=crypto.encrypt(q.member_id) if (crypto and q.member_id) else None,
            dob_enc=crypto.encrypt(q.dob) if (crypto and q.dob) else None,
            name_enc=crypto.encrypt(name) if (crypto and name) else None,
            npi=q.npi, status=result.network_status.value,
            result_jsonb=result.to_dict(), source_audit=result.source_audit, request_id=request_id)
    log.info("audit action=%s tenant=%s actor=%s payer=%s npi=%s member=%s status=%s req=%s",
             action, ctx.tenant_id, ctx.actor_id, q.payer, q.npi, mid_hash,
             result.network_status.value, request_id)
