import logging
from typing import Union

import asyncpg
from cryptography.fernet import Fernet

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Type alias accepted everywhere a DB handle is needed.
# asyncpg.Pool is used in production; asyncpg.Connection is accepted for callers
# that manage their own connection (e.g. within an explicit transaction).
DBHandle = Union[asyncpg.Pool, asyncpg.Connection]


async def create_schema(db: DBHandle) -> None:
	# Create tables
	await db.execute(
		"""
		CREATE TABLE IF NOT EXISTS journal (
			id BIGSERIAL NOT NULL,
			topic TEXT,
			text TEXT,
			data JSONB,
			message_id INTEGER,
			qos INTEGER,
			retain INTEGER,
			entrypoint TEXT NOT NULL,
			priority INTEGER NOT NULL,
			payload TEXT GENERATED ALWAYS AS (text) STORED,
			creation TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
			modified TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
			CONSTRAINT journal_pkey PRIMARY KEY (id, creation)
			)
		"""
	)

	await db.execute(
		"""
		CREATE TABLE IF NOT EXISTS "user" (
			id SERIAL PRIMARY KEY,
			username TEXT NOT NULL UNIQUE,
			password_hash BYTEA,
			refresh_token BYTEA,
			disabled BOOLEAN NOT NULL DEFAULT FALSE,
			creation TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
			modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
			owner TEXT NOT NULL,
			modified_by TEXT NOT NULL)
		"""
	)

	await db.execute(
		"""
		CREATE TABLE IF NOT EXISTS "topic" (
				id SERIAL PRIMARY KEY,
				topic TEXT NOT NULL UNIQUE,
				disabled BOOLEAN NOT NULL DEFAULT FALSE,
				creation TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
				modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
				owner TEXT NOT NULL,
				modified_by TEXT NOT NULL)
		"""
	)

	# Create indexes
	indexes = [
		'CREATE INDEX IF NOT EXISTS idx_user_username ON "user"(username)',
		"CREATE INDEX IF NOT EXISTS idx_topic_topic ON topic(topic)",
	]
	for index in indexes:
		await db.execute(index)

	# Create trigger function
	await db.execute(
		r"""
		CREATE OR REPLACE FUNCTION journal_text_to_json()
		RETURNS TRIGGER
		LANGUAGE PLPGSQL
		AS
		$$
		BEGIN
				IF NEW.data IS NULL AND NEW.text IS NOT NULL AND NEW.text SIMILAR TO '(\{|\[)%' THEN
						BEGIN
								NEW.data = NEW.text::JSON;
						EXCEPTION WHEN OTHERS THEN
								NEW.data = NULL;
						END;
				END IF;
				RETURN NEW;
		END;
		$$
		"""
	)

	# Attach trigger to journal table (idempotent via DROP IF EXISTS guard)
	await db.execute(
		"""
		DROP TRIGGER IF EXISTS journal_text_to_json_trigger ON journal;
		"""
	)
	await db.execute(
		"""
		CREATE TRIGGER journal_text_to_json_trigger
		BEFORE INSERT ON journal
		FOR EACH ROW EXECUTE FUNCTION journal_text_to_json();
		"""
	)

	# Webhook table (Standard Webhooks compliant)
	await db.execute(
		"""
		CREATE TABLE IF NOT EXISTS webhook (
			id SERIAL PRIMARY KEY,
			name VARCHAR(255) NOT NULL,
			url VARCHAR(512) NOT NULL,
			signing_secret VARCHAR(255) NOT NULL,
			disabled BOOLEAN NOT NULL DEFAULT FALSE,
			creation TIMESTAMP NOT NULL DEFAULT NOW(),
			modified TIMESTAMP NOT NULL DEFAULT NOW(),
			owner_id INTEGER NOT NULL REFERENCES "user"(id),
			modified_by_id INTEGER NOT NULL REFERENCES "user"(id)
		);
		"""
	)
	await db.execute("CREATE INDEX IF NOT EXISTS idx_webhook_disabled ON webhook(disabled)")

	await db.execute(
		"""
		CREATE TABLE IF NOT EXISTS alarm (
			id SERIAL PRIMARY KEY,
			condition TEXT NOT NULL,
			owner VARCHAR(255) NOT NULL,
			creation TIMESTAMP NOT NULL DEFAULT NOW(),
			modified TIMESTAMP NOT NULL DEFAULT NOW(),
			modified_by VARCHAR(255) NOT NULL,
			disabled BOOLEAN NOT NULL DEFAULT FALSE,
			topic VARCHAR(255) NOT NULL,
			alarm_name VARCHAR(255) NOT NULL,
			delivery_method VARCHAR(255) NOT NULL,
			webhook_id INTEGER REFERENCES webhook(id),
			forward_topic VARCHAR(255)
		);
		"""
	)

	await db.execute(
		"""
		CREATE EXTENSION IF NOT EXISTS timescaledb;
		"""
	)

	await db.execute(
		"""
	DO $$
	BEGIN
		IF NOT EXISTS (
			SELECT 1
			FROM timescaledb_information.hypertables
			WHERE hypertable_name = 'journal'
		) THEN
			PERFORM create_hypertable('journal', 'creation',
				chunk_time_interval => INTERVAL '1 day',
				if_not_exists => TRUE
			);
		END IF;
	END $$;
	"""
	)

	await db.execute(
		"""
		ALTER TABLE journal SET (
			timescaledb.compress,
			timescaledb.compress_segmentby = 'topic',
			timescaledb.compress_orderby = 'creation DESC'
		);
		"""
	)

	await db.execute(
		"""
		DO $$
		BEGIN
			PERFORM add_compression_policy('journal',
				INTERVAL '7 days',
				if_not_exists => TRUE
			);
		END $$;
		"""
	)

	await db.execute(
		"""
		DO $$
		BEGIN
			PERFORM add_retention_policy('journal',
				INTERVAL '90 days',
				if_not_exists => TRUE
			);
		END $$;
		"""
	)


async def create_admin_user(
	db: DBHandle, fernet: Fernet, admin_email: str, admin_password: str | None = None
) -> None:
	"""Create admin user if it doesn't exist"""
	exists = await db.fetchrow('SELECT id FROM "user" WHERE username = $1', admin_email)

	if not exists:
		encrypted_password = fernet.encrypt(admin_password.encode()) if admin_password else None
		await db.execute(
			"""
			INSERT INTO "user" (username, password_hash, disabled, owner, modified_by)
			VALUES ($1, $2, false, $3, $4)
			""",
			admin_email,
			encrypted_password,
			admin_email,
			admin_email,
		)
		logger.info(f"{admin_email} user created successfully")


async def create_pool(db_url: str) -> asyncpg.Pool:
	return await asyncpg.create_pool(db_url)


async def initialize_db(
	pool: asyncpg.Pool,
	fernet_key: str,
	admin_email: str,
	admin_password: str,
	mqtt_user: str,
) -> None:
	"""Initialize database with schema and admin user"""
	async with pool.acquire() as conn:
		async with conn.transaction():
			await create_schema(conn)

			if all([fernet_key, admin_email, admin_password]):
				fernet = Fernet(fernet_key)
				await create_admin_user(conn, fernet, admin_email, admin_password)

			if fernet_key and mqtt_user:
				await create_admin_user(conn, fernet, mqtt_user, None)
