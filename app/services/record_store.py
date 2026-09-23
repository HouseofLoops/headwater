"""
Owner-scoped, durable record storage for scrape jobs, monitors and webhooks.

Replaces the in-memory ``JobStore`` in ``google_maps_scraper.py``, which had
two defects that this module exists to fix:

1. **No owner field.** ``list_jobs`` and ``delete_job`` authenticate the
   caller and then call ``JobStore.list_all()`` / ``delete()``, which are not
   scoped to anyone. Any valid API key could enumerate, read and delete every
   other caller's jobs. Here, ``owner`` is part of the key, so a caller
   physically cannot address another caller's record: a cross-owner ``get``
   returns ``None`` rather than being filtered after the fact.

2. **Not durable and not shared.** A process-local dict means jobs vanish on
   restart and are invisible to sibling workers -- a job created on worker 1
   returns 404 from worker 2. Records live in Redis when it is configured.

The in-memory backend remains, but only as an explicitly non-durable
development and test fallback: :meth:`RecordStore.is_durable` reports which
backend is live so callers and health checks can tell the truth about it.

Owner identity
--------------
The owner is derived from the caller's API key via HMAC-SHA256 keyed on the
application ``SECRET_KEY``. The raw key is never stored. HMAC rather than a
bare digest matters: a plain ``sha256(api_key)`` in a leaked Redis dump is
brute-forceable against a wordlist, because API keys are often short and
human-chosen. Without ``SECRET_KEY`` the digest cannot be precomputed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.log_safety import scrub

logger = logging.getLogger(__name__)

__all__ = ["RecordStore", "StoredRecord", "get_record_store", "owner_id_for_api_key"]

# Records with no explicit expiry are dropped after this long, so an abandoned
# job cannot pin memory or a Redis key forever.
DEFAULT_TTL_SECONDS = 7 * 24 * 3600


def owner_id_for_api_key(api_key: str | None) -> str:
    """Derive a stable, non-reversible owner id from a caller's API key.

    Args:
        api_key: The caller's raw API key, or None for unauthenticated callers.

    Returns:
        A 32-character hex owner id. Unauthenticated callers all share the
        ``"anonymous"`` partition, which is deliberate: without a key there is
        no identity to isolate on, so such callers must not be able to reach
        an authenticated caller's records either.
    """
    if not api_key:
        return "anonymous"

    # Always keyed -- see app.core.identity. The previous unkeyed sha256
    # fallback was brute-forceable from a leaked store against a wordlist,
    # because API keys are short and often human-chosen.
    from app.core.identity import keyed_digest

    return keyed_digest(api_key)


@dataclass
class StoredRecord:
    """A single stored record.

    Attributes:
        id: Record identifier, unique within (namespace, owner).
        owner: Owner id from :func:`owner_id_for_api_key`.
        data: Arbitrary JSON-serialisable payload.
        created_at: Unix timestamp of creation.
        updated_at: Unix timestamp of last write.
    """

    id: str
    owner: str
    data: dict[str, Any]
    created_at: float
    updated_at: float

    def to_json(self) -> str:
        return json.dumps(
            {
                "id": self.id,
                "owner": self.owner,
                "data": self.data,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> StoredRecord:
        payload = json.loads(raw)
        return cls(
            id=payload["id"],
            owner=payload["owner"],
            data=payload["data"],
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
        )


class RecordStore:
    """Owner-scoped store over Redis, with a non-durable in-memory fallback.

    Every method takes ``owner`` and scopes to it. There is deliberately no
    "list everything" or "get by id regardless of owner" method: the previous
    implementation had exactly those, and they were the vulnerability.

    Args:
        namespace: Logical collection, e.g. ``"maps:jobs"``. Keys are
            ``{namespace}:{owner}:{record_id}``.
        ttl_seconds: Expiry applied to every record.
    """

    def __init__(self, namespace: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self.namespace = namespace.rstrip(":")
        self.ttl_seconds = ttl_seconds
        self._memory: dict[str, tuple[float, StoredRecord]] = {}
        self._lock = asyncio.Lock()
        self._redis_checked = False
        self._redis_available = False

    # -- backend selection -------------------------------------------------

    async def _get_redis(self):
        """Return a live Redis client, or None to use the memory fallback."""
        try:
            from app.core.redis_manager import RedisManager

            manager = await RedisManager.get_instance()
            # is_available is a @property. Calling it raised
            # "'bool' object is not callable" on every access, which this
            # except block swallowed into the memory fallback -- so Maps jobs,
            # monitors and webhooks were never durable, which is exactly the
            # failure this class documents as unacceptable in production.
            if not manager.is_available:
                return None
            return await manager.get_client()
        except Exception as exc:
            # Never let a storage-backend problem take down a request path;
            # but do surface it, rather than silently degrading to a backend
            # that loses data on restart.
            if not self._redis_checked:
                logger.warning("Redis unavailable for %s: %s", self.namespace, exc)
            return None
        finally:
            self._redis_checked = True

    async def is_durable(self) -> bool:
        """True when records survive a restart (Redis backend is live).

        Health checks and startup validation should refuse to run multi-worker
        production on a False result: an in-memory store silently 404s records
        created by a sibling worker.
        """
        return await self._get_redis() is not None

    def _key(self, owner: str, record_id: str) -> str:
        return f"{self.namespace}:{owner}:{record_id}"

    def _owner_pattern(self, owner: str) -> str:
        return f"{self.namespace}:{owner}:*"

    # -- CRUD --------------------------------------------------------------

    async def put(self, owner: str, record_id: str, data: dict[str, Any]) -> StoredRecord:
        """Create or replace a record owned by ``owner``."""
        now = time.time()
        existing = await self.get(owner, record_id)
        record = StoredRecord(
            id=record_id,
            owner=owner,
            data=data,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )

        client = await self._get_redis()
        if client is not None:
            await client.set(self._key(owner, record_id), record.to_json(), ex=self.ttl_seconds)
            return record

        async with self._lock:
            self._memory[self._key(owner, record_id)] = (now + self.ttl_seconds, record)
        return record

    async def get(self, owner: str, record_id: str) -> StoredRecord | None:
        """Return the record if it exists AND belongs to ``owner``.

        A record owned by someone else is indistinguishable from one that does
        not exist. That is intentional -- a 404-vs-403 distinction would let a
        caller enumerate other owners' record ids.
        """
        client = await self._get_redis()
        if client is not None:
            raw = await client.get(self._key(owner, record_id))
            if raw is None:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode()
            try:
                return StoredRecord.from_json(raw)
            except (ValueError, KeyError) as exc:
                # The owner is intentionally absent: it derives from the
                # caller's API key, and record_id alone identifies the row for
                # diagnosis. Log aggregation is a different trust boundary
                # from Redis.
                logger.error("Corrupt record %s: %s", scrub(record_id), scrub(exc))
                return None

        async with self._lock:
            entry = self._memory.get(self._key(owner, record_id))
            if entry is None:
                return None
            expires_at, record = entry
            if expires_at < time.time():
                self._memory.pop(self._key(owner, record_id), None)
                return None
            return record

    async def delete(self, owner: str, record_id: str) -> bool:
        """Delete a record. Returns False if it does not exist for ``owner``."""
        client = await self._get_redis()
        if client is not None:
            return bool(await client.delete(self._key(owner, record_id)))

        async with self._lock:
            return self._memory.pop(self._key(owner, record_id), None) is not None

    async def list_for_owner(
        self,
        owner: str,
        *,
        limit: int = 50,
        offset: int = 0,
        predicate: Callable[[StoredRecord], bool] | None = None,
        sort_key: Callable[[StoredRecord], Any] | None = None,
        reverse: bool = True,
    ) -> list[StoredRecord]:
        """List ``owner``'s records, newest first by default.

        Args:
            owner: Owner id. Only this owner's records are ever returned.
            limit: Maximum records to return.
            offset: Pagination offset.
            predicate: Optional filter, e.g. by status.
            sort_key: Sort key; defaults to ``created_at``.
            reverse: Sort descending (newest first) when True.
        """
        records: list[StoredRecord] = []
        client = await self._get_redis()

        if client is not None:
            # scan_iter, not keys(): KEYS blocks the Redis event loop across
            # the whole keyspace and is a production hazard.
            try:
                async for key in client.scan_iter(match=self._owner_pattern(owner), count=200):
                    raw = await client.get(key)
                    if not raw:
                        continue
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    try:
                        records.append(StoredRecord.from_json(raw))
                    except ValueError, KeyError:
                        continue
            except Exception as exc:
                logger.error("Listing %s for owner failed: %s", self.namespace, exc)
                raise
        else:
            now = time.time()
            prefix = f"{self.namespace}:{owner}:"
            async with self._lock:
                expired = [k for k, (exp, _) in self._memory.items() if exp < now]
                for k in expired:
                    self._memory.pop(k, None)
                records = [r for k, (_, r) in self._memory.items() if k.startswith(prefix)]

        if predicate is not None:
            records = [r for r in records if predicate(r)]

        records.sort(key=sort_key or (lambda r: r.created_at), reverse=reverse)
        return records[offset : offset + limit]

    async def count_for_owner(self, owner: str) -> int:
        """Number of records ``owner`` currently holds (for quota enforcement)."""
        return len(await self.list_for_owner(owner, limit=10_000, offset=0))


_stores: dict[str, RecordStore] = {}


def get_record_store(namespace: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> RecordStore:
    """Return the process-wide store for ``namespace``, creating it if needed."""
    store = _stores.get(namespace)
    if store is None:
        store = RecordStore(namespace, ttl_seconds=ttl_seconds)
        _stores[namespace] = store
    return store
