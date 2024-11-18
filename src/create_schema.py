import logging

from cryptography.fernet import Fernet
from databases import Database
from environs import Env

logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger(__name__)


async def create_schema(db: Database, fernet: Fernet | None = None) -> None:
	# Create tables
	await db.execute(
		"""
		CREATE TABLE IF NOT EXISTS journal (
			id BIGSERIAL NOT NULL,
			topic VARCHAR(256),
			text VARCHAR(4096),
			data JSONB,
			message_id INTEGER,
			qos INTEGER,
			retain INTEGER,
			entrypoint TEXT NOT NULL,
			priority INTEGER NOT NULL,
			payload VARCHAR(4096) GENERATED ALWAYS AS (text) STORED,
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

	# Create indexes - split into separate statements
	indexes = [
		'CREATE INDEX IF NOT EXISTS idx_user_username ON "user"(username)',
		"CREATE INDEX IF NOT EXISTS idx_topic_topic ON topic(topic)",
	]

	for index in indexes:
		await db.execute(index)

	# Create function
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

	# Drop and create trigger separately
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
			delivery_method VARCHAR(255) NOT NULL
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

	# After creating the journal table and converting to hypertable,
	# enable compression and set configuration
	await db.execute(
		"""
		ALTER TABLE journal SET (
			timescaledb.compress,
			timescaledb.compress_segmentby = 'topic',
			timescaledb.compress_orderby = 'creation DESC'
		);
		"""
	)

	# Add compression policy
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

	# Add retention policy
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
	db: Database, fernet: Fernet, admin_email: str, admin_password: str | None = None
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
		_logger.info(f"{admin_email} user created successfully")


def TimescaleDB(**kwargs: str) -> Database:
	env = Env()
	env.read_env()

	db_user = kwargs.get("db_user") or env.str("DB_USER")
	db_password = kwargs.get("db_user") or env.str("DB_PASSWORD")
	db_host = kwargs.get("db_host") or env.str("DB_HOST")
	db_port = kwargs.get("db_port") or env.str("DB_PORT", "5432")
	db_name = kwargs.get("db_name") or env.str("DB_NAME")

	db_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
	return Database(db_url)


async def initialize_db() -> None:
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

			# Create MQTT service account
			mqtt_user = env.str("MQTT_USER")
			if fernet_key and mqtt_user:
				await create_admin_user(db, fernet, mqtt_user, None)

	finally:
		await db.disconnect()
