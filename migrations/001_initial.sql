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
);

CREATE TABLE IF NOT EXISTS "user" (
	id SERIAL PRIMARY KEY,
	username TEXT NOT NULL UNIQUE,
	password_hash TEXT,
	refresh_token BYTEA,
	disabled BOOLEAN NOT NULL DEFAULT FALSE,
	is_admin BOOLEAN NOT NULL DEFAULT FALSE,
	creation TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
	modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
	owner TEXT NOT NULL,
	modified_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topic (
	id SERIAL PRIMARY KEY,
	topic TEXT NOT NULL UNIQUE,
	disabled BOOLEAN NOT NULL DEFAULT FALSE,
	creation TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
	modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
	owner TEXT NOT NULL,
	modified_by TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_username ON "user"(username);
CREATE INDEX IF NOT EXISTS idx_topic_topic ON topic(topic);

CREATE OR REPLACE FUNCTION journal_text_to_json()
RETURNS TRIGGER
LANGUAGE PLPGSQL
AS $$
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
$$;

DROP TRIGGER IF EXISTS journal_text_to_json_trigger ON journal;
CREATE TRIGGER journal_text_to_json_trigger
BEFORE INSERT ON journal
FOR EACH ROW EXECUTE FUNCTION journal_text_to_json();

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

CREATE INDEX IF NOT EXISTS idx_webhook_disabled ON webhook(disabled);

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

CREATE EXTENSION IF NOT EXISTS timescaledb;

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

ALTER TABLE journal SET (
	timescaledb.compress,
	timescaledb.compress_segmentby = 'topic',
	timescaledb.compress_orderby = 'creation DESC'
);

DO $$
BEGIN
	PERFORM add_compression_policy('journal',
		INTERVAL '7 days',
		if_not_exists => TRUE
	);
END $$;

DO $$
BEGIN
	PERFORM add_retention_policy('journal',
		INTERVAL '90 days',
		if_not_exists => TRUE
	);
END $$;

CREATE TABLE IF NOT EXISTS pid_state (
	namespace TEXT NOT NULL DEFAULT 'default',
	controller_id TEXT NOT NULL,
	previous_error DOUBLE PRECISION NOT NULL DEFAULT 0,
	integral DOUBLE PRECISION NOT NULL DEFAULT 0,
	last_time DOUBLE PRECISION NOT NULL DEFAULT 0,
	output DOUBLE PRECISION NOT NULL DEFAULT 0,
	PRIMARY KEY (namespace, controller_id)
);

CREATE TABLE IF NOT EXISTS revoked_token (
	jti TEXT PRIMARY KEY,
	expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_revoked_token_expires_at ON revoked_token(expires_at);
