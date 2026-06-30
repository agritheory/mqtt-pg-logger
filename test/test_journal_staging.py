"""Tests for journal staging + promote."""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from src.migrate import run_migrations


@pytest.fixture  # type: ignore[misc]
async def migrated_pool(db_url: str) -> asyncpg.Pool:
	pool = await asyncpg.create_pool(db_url)
	async with pool.acquire() as conn:
		await run_migrations(conn)
	yield pool
	await pool.close()


@pytest.fixture(autouse=True)  # type: ignore[misc]
async def clean_staging_tables(migrated_pool: asyncpg.Pool) -> None:
	await migrated_pool.execute(
		"""
		TRUNCATE journal, journal_staging, journal_rejected RESTART IDENTITY CASCADE
		"""
	)


@pytest.mark.asyncio  # type: ignore[misc]
async def test_promote_all_good(migrated_pool: asyncpg.Pool) -> None:
	batch_id = uuid.uuid4()
	await migrated_pool.execute(
		"""
		INSERT INTO journal_staging
		(batch_id, topic, text, qos, retain, entrypoint, priority, status)
		SELECT $1, 'sensors/t1', '{"v":1}', 1, false, 'mqtt', 0, 'pending'
		FROM generate_series(1, 5)
		""",
		batch_id,
	)
	row = await migrated_pool.fetchrow(
		"SELECT promoted, rejected FROM promote_journal_batch($1)",
		batch_id,
	)
	assert int(row["promoted"]) == 5
	assert int(row["rejected"]) == 0
	journal_count = await migrated_pool.fetchval("SELECT COUNT(*) FROM journal")
	assert journal_count == 5


@pytest.mark.asyncio  # type: ignore[misc]
async def test_promote_partial_rejection(migrated_pool: asyncpg.Pool) -> None:
	batch_id = uuid.uuid4()
	await migrated_pool.executemany(
		"""
		INSERT INTO journal_staging
		(batch_id, topic, text, qos, retain, entrypoint, priority, status)
		VALUES ($1, $2, $3, 1, false, 'mqtt', 0, 'pending')
		""",
		[
			(batch_id, "sensors/good", '{"ok": true}'),
			(batch_id, "sensors/bad", "__reject__"),
			(batch_id, "sensors/good2", '{"ok": true}'),
		],
	)
	row = await migrated_pool.fetchrow(
		"SELECT promoted, rejected FROM promote_journal_batch($1)",
		batch_id,
	)
	assert int(row["promoted"]) == 2
	assert int(row["rejected"]) == 1
	rejected_count = await migrated_pool.fetchval("SELECT COUNT(*) FROM journal_rejected")
	assert rejected_count == 1


@pytest.mark.asyncio  # type: ignore[misc]
async def test_promote_idempotent(migrated_pool: asyncpg.Pool) -> None:
	batch_id = uuid.uuid4()
	await migrated_pool.execute(
		"""
		INSERT INTO journal_staging
		(batch_id, topic, text, qos, retain, entrypoint, priority, status)
		VALUES ($1, 'sensors/t1', '{"v":1}', 1, false, 'mqtt', 0, 'pending')
		""",
		batch_id,
	)
	first = await migrated_pool.fetchrow(
		"SELECT promoted, rejected FROM promote_journal_batch($1)",
		batch_id,
	)
	second = await migrated_pool.fetchrow(
		"SELECT promoted, rejected FROM promote_journal_batch($1)",
		batch_id,
	)
	assert int(first["promoted"]) == 1
	assert int(second["promoted"]) == 0
	assert int(second["rejected"]) == 0
	journal_count = await migrated_pool.fetchval("SELECT COUNT(*) FROM journal")
	assert journal_count == 1
