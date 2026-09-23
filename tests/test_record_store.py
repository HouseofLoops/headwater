"""Tests for the owner-scoped record store.

The security property under test is cross-owner isolation: the store this
replaces let any valid API key list, read and delete every other caller's
scrape jobs.
"""

from unittest.mock import patch

import pytest

from app.services.record_store import (
    RecordStore,
    owner_id_for_api_key,
)


@pytest.fixture
def store():
    """A store forced onto the in-memory backend (no Redis in tests)."""
    s = RecordStore("test:ns", ttl_seconds=60)
    with patch.object(RecordStore, "_get_redis", return_value=None):
        yield s


@pytest.mark.asyncio
class TestOwnerIsolation:
    """The vulnerability this module exists to close."""

    async def test_owner_cannot_read_another_owners_record(self, store):
        await store.put("alice", "job1", {"secret": "alice-data"})
        assert await store.get("bob", "job1") is None

    async def test_owner_cannot_delete_another_owners_record(self, store):
        await store.put("alice", "job1", {"x": 1})
        assert await store.delete("bob", "job1") is False
        # Alice's record is untouched.
        assert (await store.get("alice", "job1")).data == {"x": 1}

    async def test_listing_returns_only_own_records(self, store):
        await store.put("alice", "a1", {"n": 1})
        await store.put("alice", "a2", {"n": 2})
        await store.put("bob", "b1", {"n": 3})

        alice = await store.list_for_owner("alice")
        bob = await store.list_for_owner("bob")

        assert {r.id for r in alice} == {"a1", "a2"}
        assert {r.id for r in bob} == {"b1"}

    async def test_same_record_id_is_independent_per_owner(self, store):
        # Two owners may legitimately hold the same id; neither leaks.
        await store.put("alice", "shared", {"who": "alice"})
        await store.put("bob", "shared", {"who": "bob"})
        assert (await store.get("alice", "shared")).data == {"who": "alice"}
        assert (await store.get("bob", "shared")).data == {"who": "bob"}

    async def test_missing_and_forbidden_are_indistinguishable(self, store):
        # Both return None, so a caller cannot enumerate others' record ids.
        await store.put("alice", "exists", {})
        assert await store.get("bob", "exists") is None
        assert await store.get("bob", "never-existed") is None

    async def test_anonymous_is_its_own_partition(self, store):
        await store.put("anonymous", "j", {"n": 1})
        assert await store.get(owner_id_for_api_key("real-key"), "j") is None


@pytest.mark.asyncio
class TestCrud:
    async def test_put_then_get_round_trips(self, store):
        await store.put("alice", "j", {"nested": {"a": [1, 2]}})
        assert (await store.get("alice", "j")).data == {"nested": {"a": [1, 2]}}

    async def test_put_replaces_and_preserves_created_at(self, store):
        first = await store.put("alice", "j", {"v": 1})
        second = await store.put("alice", "j", {"v": 2})
        assert second.data == {"v": 2}
        assert second.created_at == first.created_at
        assert second.updated_at >= first.updated_at

    async def test_delete_returns_false_for_missing(self, store):
        assert await store.delete("alice", "nope") is False

    async def test_get_missing_returns_none(self, store):
        assert await store.get("alice", "nope") is None

    async def test_expired_record_is_not_returned(self, store):
        await store.put("alice", "j", {"v": 1})
        # Force expiry rather than sleeping.
        key = store._key("alice", "j")
        _expires_at, record = store._memory[key]
        store._memory[key] = (0.0, record)
        assert await store.get("alice", "j") is None


@pytest.mark.asyncio
class TestListing:
    async def test_newest_first_by_default(self, store):
        for i in range(3):
            rec = await store.put("alice", f"j{i}", {"n": i})
            rec.created_at = float(i)  # deterministic ordering
            store._memory[store._key("alice", f"j{i}")] = (1e12, rec)
        ids = [r.id for r in await store.list_for_owner("alice")]
        assert ids == ["j2", "j1", "j0"]

    async def test_predicate_filters(self, store):
        await store.put("alice", "a", {"status": "done"})
        await store.put("alice", "b", {"status": "running"})
        done = await store.list_for_owner("alice", predicate=lambda r: r.data.get("status") == "done")
        assert [r.id for r in done] == ["a"]

    async def test_limit_and_offset(self, store):
        for i in range(5):
            await store.put("alice", f"j{i}", {"n": i})
        assert len(await store.list_for_owner("alice", limit=2)) == 2
        assert len(await store.list_for_owner("alice", limit=2, offset=4)) == 1

    async def test_count_is_owner_scoped(self, store):
        await store.put("alice", "a", {})
        await store.put("bob", "b", {})
        assert await store.count_for_owner("alice") == 1


class TestOwnerIdDerivation:
    def test_raw_key_never_appears_in_owner_id(self):
        key = "super-secret-api-key"
        assert key not in owner_id_for_api_key(key)

    def test_is_deterministic(self):
        assert owner_id_for_api_key("k") == owner_id_for_api_key("k")

    def test_different_keys_give_different_owners(self):
        assert owner_id_for_api_key("k1") != owner_id_for_api_key("k2")

    def test_none_and_empty_map_to_anonymous(self):
        assert owner_id_for_api_key(None) == "anonymous"
        assert owner_id_for_api_key("") == "anonymous"

    def test_owner_id_is_fixed_width(self):
        assert len(owner_id_for_api_key("anything")) == 32


@pytest.mark.asyncio
class TestDurabilityIsHonest:
    async def test_reports_non_durable_without_redis(self, store):
        # Callers and health checks must be able to tell that records will not
        # survive a restart, rather than silently degrading.
        assert await store.is_durable() is False
