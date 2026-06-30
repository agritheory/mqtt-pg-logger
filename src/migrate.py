import logging
from pathlib import Path
from typing import Union

import asyncpg

logger = logging.getLogger(__name__)

DBHandle = Union[asyncpg.Pool, asyncpg.Connection]

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


async def ensure_migrations_table(db: DBHandle) -> None:
	await db.execute(
		"""
		CREATE TABLE IF NOT EXISTS schema_migrations (
			version INT PRIMARY KEY,
			applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
		)
		"""
	)


async def applied_versions(db: DBHandle) -> set[int]:
	rows = await db.fetch("SELECT version FROM schema_migrations ORDER BY version")
	return {int(row["version"]) for row in rows}


def migration_files() -> list[tuple[int, Path]]:
	files: list[tuple[int, Path]] = []
	for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
		version = int(path.stem.split("_", 1)[0])
		files.append((version, path))
	return files


async def run_migrations(db: DBHandle) -> None:
	if isinstance(db, asyncpg.Pool):
		async with db.acquire() as conn:
			await run_migrations(conn)
		return

	await ensure_migrations_table(db)
	done = await applied_versions(db)

	for version, path in migration_files():
		if version in done:
			continue
		sql = path.read_text(encoding="utf-8")
		logger.info("Applying migration %s (%s)", version, path.name)
		await db.execute(sql)
		await db.execute(
			"INSERT INTO schema_migrations (version) VALUES ($1)",
			version,
		)
		logger.info("Applied migration %s", version)
