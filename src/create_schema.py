import logging

from cryptography.fernet import Fernet
from databases import Database
from environs import Env

logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger(__name__)


async def create_schema(db: Database, fernet: Fernet | None = None) -> None:
	"""Create complete database schema including admin user"""

	# Create ENUM
	await db.execute(
		"""
		DO $$
		BEGIN
			IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'pgqueuer_status') THEN
				CREATE TYPE pgqueuer_status AS ENUM ('queued', 'picked');
			END IF;

			IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'pgqueuer_statistics_status') THEN
				CREATE TYPE pgqueuer_statistics_status AS ENUM ('exception', 'successful');
			END IF;
		END $$;
		"""
	)

	# Create tables
	await db.execute(
		"""
		CREATE TABLE IF NOT EXISTS pgqueuer (
			id BIGSERIAL NOT NULL,
			topic VARCHAR(256),
			text VARCHAR(4096),
			data JSONB,
			message_id INTEGER,
			qos INTEGER,
			retain INTEGER,
			entrypoint TEXT NOT NULL,
			priority INTEGER NOT NULL,
			status pgqueuer_status NOT NULL,
			payload VARCHAR(4096) GENERATED ALWAYS AS (text) STORED,
			creation TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
			modified TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
			CONSTRAINT pgqueuer_pkey PRIMARY KEY (id, creation)
			)
		"""
	)

	await db.execute(
		"""
		CREATE TABLE IF NOT EXISTS pgqueuer_statistics (
			id SERIAL PRIMARY KEY,
			created TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT DATE_TRUNC('sec', NOW() at time zone 'UTC'),
			count BIGINT NOT NULL,
			priority INTEGER NOT NULL,
			time_in_queue INTERVAL NOT NULL,
			status pgqueuer_statistics_status NOT NULL,
			entrypoint TEXT NOT NULL)
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

	# Create indexes - split into separate statements
	indexes = [
		"CREATE INDEX IF NOT EXISTS pgqueuer_name_idx ON pgqueuer (topic)",
		'CREATE INDEX IF NOT EXISTS idx_user_username ON "user"(username)',
		"CREATE INDEX IF NOT EXISTS idx_topic_topic ON topic(topic)",
	]

	for index in indexes:
		await db.execute(index)

	# Create function
	await db.execute(
		r"""
		CREATE OR REPLACE FUNCTION pgqueuer_text_to_json()
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

	# Drop and create trigger separately
	await db.execute(
		r"""
		CREATE OR REPLACE FUNCTION pgqueuer_text_to_json()
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
			WHERE hypertable_name = 'pgqueuer'
		) THEN
			PERFORM create_hypertable('pgqueuer', 'creation',
				chunk_time_interval => INTERVAL '1 day',
				if_not_exists => TRUE
			);
		END IF;
	END $$;
	"""
	)

	# After creating the pgqueuer table and converting to hypertable,
	# enable compression and set configuration
	await db.execute(
		"""
		ALTER TABLE pgqueuer SET (
			timescaledb.compress,
			timescaledb.compress_segmentby = 'topic,entrypoint,status',
			timescaledb.compress_orderby = 'creation DESC'
		);
		"""
	)

	# Add compression policy
	await db.execute(
		"""
		DO $$
		BEGIN
			PERFORM add_compression_policy('pgqueuer',
				INTERVAL '7 days',
				if_not_exists => TRUE
			);
		END $$;
		"""
	)

	# Add retention policy
	await db.execute(
		"""
		DO $$
		BEGIN
			PERFORM add_retention_policy('pgqueuer',
				INTERVAL '90 days',
				if_not_exists => TRUE
			);
		END $$;
		"""
	)


async def create_admin_user(
	db: Database, fernet: Fernet, admin_email: str, admin_password: str
) -> None:
	"""Create admin user if it doesn't exist"""
	query = 'SELECT id FROM "user" WHERE username = :username'
	exists = await db.fetch_one(query=query, values={"username": admin_email})

	if not exists:
		# The encrypted password is already bytes, don't decode it
		encrypted_password = fernet.encrypt(admin_password.encode()) if admin_password else None
		query = """
		INSERT INTO "user" (username, password_hash, disabled, owner, modified_by)
		VALUES (:username, :password, false, :owner, :modified_by)
		"""
		await db.execute(
			query=query,
			values={
				"username": admin_email,
				"password": encrypted_password,
				"owner": admin_email,
				"modified_by": admin_email,
			},
		)
		_logger.info("Admin user created successfully")


def TimescaleDB() -> Database:
	env = Env()
	env.read_env()

	db_url = (
		f"postgresql://{env.str('DB_USER')}:{env.str('DB_PASSWORD')}"
		f"@{env.str('DB_HOST')}:{env.str('DB_PORT', '5432')}"
		f"/{env.str('DB_NAME')}"
	)
	return Database(db_url)


async def initialize_db():
	"""Initialize database with schema and admin user"""
	env = Env()
	env.read_env()
	db = TimescaleDB()
	await db.connect()

	try:
		async with db.transaction():
			await create_schema(db)

			# Create admin user if credentials provided
			fernet_key = env.str("FERNET_KEY", None)
			admin_email = env.str("ADMIN_EMAIL", None)
			admin_password = env.str("ADMIN_PASSWORD", None)

			if all([fernet_key, admin_email, admin_password]):
				fernet = Fernet(fernet_key)
				await create_admin_user(db, fernet, admin_email, admin_password)

			mqtt_user = env.str("MQTT_USER")
			if fernet_key and mqtt_user:
				await create_admin_user(db, fernet, mqtt_user, None)

	finally:
		await db.disconnect()
