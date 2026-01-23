import os, time, uuid

APPROVAL_SECRET = os.getenv("APPROVAL_SECRET", "")

# In-memory approvals (MVP). Replace with Redis/Postgres for persistence.
_APPROVALS = {}

def request_approval(action: str, summary: str, risk: str, artifacts=None):
    approval_id = str(uuid.uuid4())
    _APPROVALS[approval_id] = {
        "id": approval_id,
        "action": action,
        "summary": summary,
        "risk": risk,
        "artifacts": artifacts or [],
        "status": "pending",
        "created_at": int(time.time()),
        "decided_at": None,
    }
    return {"ok": True, "approval_id": approval_id, "status": "pending"}

def check_approval(approval_id: str):
    a = _APPROVALS.get(approval_id)
    if not a:
        return {"ok": False, "error": "unknown approval_id"}
    return {"ok": True, "approval": a}

# OPTIONAL: you can add tiny endpoints later for a human UI to approve/deny:
# approve(approval_id), deny(approval_id)
