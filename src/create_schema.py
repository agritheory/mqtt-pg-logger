import logging

from cryptography.fernet import Fernet
from databases import Database

logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger(__name__)


async def create_schema(db: Database, fernet: Fernet | None = None) -> None:
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

	# Create indexes - split into separate statements
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
			webhook_url VARCHAR(512)
		);
		"""
	)

	# Migrate existing deployments that pre-date the webhook_url column
	await db.execute(
		"""
		ALTER TABLE alarm ADD COLUMN IF NOT EXISTS webhook_url VARCHAR(512);
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


def TimescaleDB(
	db_user: str,
	db_password: str,
	db_host: str,
	db_port: str,
	db_name: str,
	force_rollback: bool = False,
) -> Database:
	db_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
	return Database(db_url, force_rollback=force_rollback)


async def initialize_db(
	db: Database, fernet_key: str, admin_email: str, admin_password: str, mqtt_user: str
) -> None:
	"""Initialize database with schema and admin user"""
	async with db.transaction():
		await create_schema(db)

		if all([fernet_key, admin_email, admin_password]):
			fernet = Fernet(fernet_key)
			await create_admin_user(db, fernet, admin_email, admin_password)

		# Create MQTT service account
		if fernet_key and mqtt_user:
			await create_admin_user(db, fernet, mqtt_user, None)
