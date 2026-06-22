import logging
from typing import TypeAlias

import asyncpg

from src.migrate import run_migrations
from src.passwords import hash_password

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DBHandle: TypeAlias = asyncpg.Pool | asyncpg.Connection


async def create_admin_user(
	db: DBHandle,
	admin_email: str,
	admin_password: str | None = None,
	*,
	is_admin: bool = False,
) -> None:
	"""Create admin user if it doesn't exist."""
	exists = await db.fetchrow('SELECT id FROM "user" WHERE username = $1', admin_email)

	if not exists:
		password_hash = hash_password(admin_password) if admin_password else None
		await db.execute(
			"""
			INSERT INTO "user" (username, password_hash, disabled, is_admin, owner, modified_by)
			VALUES ($1, $2, false, $3, $4, $5)
			""",
			admin_email,
			password_hash,
			is_admin,
			admin_email,
			admin_email,
		)
		logger.info("%s user created successfully", admin_email)


async def create_pool(db_url: str) -> asyncpg.Pool:
	return await asyncpg.create_pool(db_url)


async def initialize_db(
	pool: asyncpg.Pool,
	admin_email: str | None,
	admin_password: str | None,
	mqtt_user: str | None,
) -> None:
	"""Initialize database schema via migrations and seed users."""
	async with pool.acquire() as conn:
		await run_migrations(conn)

	async with pool.acquire() as conn:
		if admin_email and admin_password:
			await create_admin_user(
				conn,
				admin_email,
				admin_password,
				is_admin=True,
			)

		if mqtt_user:
			await create_admin_user(conn, mqtt_user, None, is_admin=False)


async def prune_expired_revoked_tokens(pool: asyncpg.Pool) -> None:
	await pool.execute("DELETE FROM revoked_token WHERE expires_at < NOW()")
