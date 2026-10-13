"""Cross-process per-principal rate limits stored atomically in the app database.

On PostgreSQL the upsert works across API replicas. SQLite is for one-user demos;
production must additionally use an ingress/WAF limit to stop unauthenticated floods.
"""
from __future__ import annotations

import hashlib
import os
import time

from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db.models import RequestLimitRow


def enforce_limit(db: Session, *, org: str, principal: str, role: str) -> None:
    try:
        capacity = int(os.getenv("ADVISORY_REQUESTS_PER_MINUTE", "120"))
    except ValueError:
        raise HTTPException(status_code=503, detail="Invalid rate limit configuration") from None
    if not 1 <= capacity <= 10000:
        raise HTTPException(status_code=503, detail="Invalid rate limit configuration")
    bucket = int(time.time()) // 60
    # Hash identities to avoid storing subjects as rate-limit keys.
    key = hashlib.sha256(f"{org}:{role}:{principal}".encode()).hexdigest()
    dialect = db.bind.dialect.name
    if dialect == "postgresql":
        stmt = pg_insert(RequestLimitRow)
    elif dialect == "sqlite":
        stmt = sqlite_insert(RequestLimitRow)
    else:
        # Do not silently fall back to an unsafe in-process limiter.
        raise HTTPException(status_code=503, detail="Rate limiter requires PostgreSQL or SQLite")
    stmt = stmt.values(identity_hash=key, minute_bucket=bucket, requests=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=[RequestLimitRow.identity_hash, RequestLimitRow.minute_bucket],
        set_={"requests": RequestLimitRow.requests + 1},
    ).returning(RequestLimitRow.requests)
    try:
        count = db.execute(stmt).scalar_one()
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=503, detail="Rate limiter unavailable") from None
    if count > capacity:
        raise HTTPException(status_code=429, detail="Request rate limit exceeded", headers={"Retry-After": str(60 - int(time.time()) % 60)})
