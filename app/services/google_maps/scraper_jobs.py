"""
Scrape job model and the owner-scoped job store.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from app.services.record_store import RecordStore, get_record_store

#: Namespace for scrape jobs in the owner-scoped record store.
JOB_NAMESPACE = "maps:jobs"


class JobStatus(str, Enum):
    """Status of a scraping job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ScrapeJob:
    """Represents a scraping job.

    ``owner`` is the field that makes cross-tenant access impossible rather
    than merely unlikely: it becomes part of the storage key, so one caller
    cannot address another caller's job at all. It defaults to empty only so
    that the dataclass stays keyword-constructible; :class:`JobStore` refuses
    to persist a job without one rather than quietly filing it under a shared
    partition.
    """

    id: str
    name: str
    query: str
    owner: str = ""
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    results: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    #: True when the job failed because Google's markup changed rather than
    #: because of a transient error; routers should surface this as 503.
    selectors_stale: bool = False
    #: True when the job completed with zero results that could NOT be
    #: confirmed as a genuine zero (the results feed rendered but nothing
    #: inside it matched). The job is not failed, but callers must not treat
    #: the empty list as authoritative.
    empty_unverified: bool = False
    progress: int = 0
    total: int = 0

    # Job parameters
    language: str = "en"
    max_results: int = 20
    zoom: int = 15
    geo_coordinates: str | None = None
    email_extraction: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "ID": self.id,  # gosom compatibility
            "name": self.name,
            "Name": self.name,
            "query": self.query,
            "status": self.status.value,
            "Status": "ok" if self.status == JobStatus.COMPLETED else self.status.value,
            "created_at": self.created_at.isoformat(),
            "Date": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "progress": self.progress,
            "total": self.total,
            "error": self.error,
            # Lets a router turn a markup-rotation failure into a 503 rather
            # than reporting a job that merely "found nothing".
            "selectors_stale": self.selectors_stale,
            "empty_unverified": self.empty_unverified,
            "Data": {
                "keywords": [self.query],
                "lang": self.language,
                "zoom": self.zoom,
            },
        }

    def to_record(self) -> dict[str, Any]:
        """Full, lossless payload for the record store.

        Distinct from :meth:`to_dict`, which is the lossy gosom-compatible API
        shape. Persisting ``to_dict`` would drop ``results``, ``owner`` and the
        job parameters, so a job reloaded after a restart would come back
        empty -- exactly the silent data loss this change exists to stop.
        """
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["created_at"] = self.created_at.isoformat()
        payload["completed_at"] = self.completed_at.isoformat() if self.completed_at else None
        return payload

    @classmethod
    def from_record(cls, payload: dict[str, Any]) -> ScrapeJob:
        """Rebuild a job from :meth:`to_record` output."""
        data = dict(payload)
        data["status"] = JobStatus(data.get("status", JobStatus.PENDING.value))
        created_at = data.get("created_at")
        data["created_at"] = datetime.fromisoformat(created_at) if created_at else datetime.now()
        completed_at = data.get("completed_at")
        data["completed_at"] = datetime.fromisoformat(completed_at) if completed_at else None
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


class JobStore:
    """Owner-scoped, durable job storage.

    Every method takes the owner explicitly (or reads it off the job), and
    there is deliberately no ``list_all``: the previous implementation had one,
    and it was the vulnerability -- ``list_jobs``/``delete_job`` authenticated
    the caller and then operated on the whole keyspace.

    Durability and cross-worker visibility come from
    :class:`~app.services.record_store.RecordStore`, which uses Redis when it
    is configured and an explicitly non-durable dict when it is not. The
    previous in-process dict and its ``asyncio.Lock`` are gone; the record
    store keeps the equivalent locking for its own memory backend.
    """

    def __init__(self, store: RecordStore | None = None) -> None:
        self._store = store if store is not None else get_record_store(JOB_NAMESPACE)

    @staticmethod
    def _require_owner(job: ScrapeJob) -> str:
        if not job.owner:
            raise ValueError(
                "ScrapeJob.owner is required; derive it with owner_id_for_api_key(api_key) before creating a job"
            )
        return job.owner

    async def is_durable(self) -> bool:
        """True when jobs survive a restart and are visible to sibling workers."""
        return await self._store.is_durable()

    async def create(self, job: ScrapeJob) -> ScrapeJob:
        """Create a job. The owner is taken from ``job.owner`` and required."""
        owner = self._require_owner(job)
        await self._store.put(owner, job.id, job.to_record())
        return job

    async def get(self, owner: str, job_id: str) -> ScrapeJob | None:
        """Return the job only if ``owner`` owns it, else None.

        A job belonging to someone else is indistinguishable from one that does
        not exist, so a caller cannot probe for other tenants' job ids.
        """
        record = await self._store.get(owner, job_id)
        if record is None:
            return None
        return ScrapeJob.from_record(record.data)

    async def update(self, job: ScrapeJob) -> ScrapeJob:
        """Persist a mutated job under its own owner."""
        owner = self._require_owner(job)
        await self._store.put(owner, job.id, job.to_record())
        return job

    async def delete(self, owner: str, job_id: str) -> bool:
        """Delete one of ``owner``'s jobs. False if they do not have it."""
        return await self._store.delete(owner, job_id)

    async def list_for_owner(
        self,
        owner: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ScrapeJob]:
        """List ``owner``'s jobs, newest first, optionally filtered by status."""
        predicate = None
        if status:

            def _status_matches(record) -> bool:
                return record.data.get("status") == status

            predicate = _status_matches
        records = await self._store.list_for_owner(owner, limit=limit, offset=offset, predicate=predicate)
        return [ScrapeJob.from_record(r.data) for r in records]
