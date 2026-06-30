-- Upgrade databases created before migrations used TEXT password_hash.
DO $$
BEGIN
	IF EXISTS (
		SELECT 1
		FROM information_schema.columns
		WHERE table_schema = 'public'
		  AND table_name = 'user'
		  AND column_name = 'password_hash'
		  AND udt_name = 'bytea'
	) THEN
		ALTER TABLE "user"
			ALTER COLUMN password_hash TYPE TEXT
			USING CASE
				WHEN password_hash IS NULL THEN NULL
				ELSE encode(password_hash, 'base64')
			END;
	END IF;
END $$;

ALTER TABLE "user" ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE;

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
