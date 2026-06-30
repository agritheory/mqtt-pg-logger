CREATE UNLOGGED TABLE IF NOT EXISTS journal_staging (
	staging_id BIGSERIAL PRIMARY KEY,
	batch_id UUID NOT NULL,
	received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
	topic TEXT NOT NULL,
	text TEXT,
	qos INTEGER NOT NULL,
	retain BOOLEAN NOT NULL,
	entrypoint TEXT NOT NULL DEFAULT 'mqtt',
	priority INTEGER NOT NULL DEFAULT 0,
	status TEXT NOT NULL DEFAULT 'pending'
		CHECK (status IN ('pending', 'promoted', 'rejected')),
	promoted_at TIMESTAMPTZ,
	reject_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_journal_staging_pending
	ON journal_staging (batch_id)
	WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS journal_rejected (
	rejected_id BIGSERIAL PRIMARY KEY,
	staging_id BIGINT,
	batch_id UUID NOT NULL,
	rejected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
	reason TEXT NOT NULL,
	topic TEXT,
	text TEXT,
	qos INTEGER,
	retain BOOLEAN,
	entrypoint TEXT,
	priority INTEGER
);

CREATE OR REPLACE FUNCTION promote_journal_batch(p_batch_id UUID)
RETURNS TABLE(promoted BIGINT, rejected BIGINT)
LANGUAGE plpgsql
AS $$
DECLARE
	r journal_staging%ROWTYPE;
	v_promoted BIGINT := 0;
	v_rejected BIGINT := 0;
BEGIN
	FOR r IN
		SELECT *
		FROM journal_staging
		WHERE batch_id = p_batch_id
		  AND status = 'pending'
		ORDER BY staging_id
		FOR UPDATE SKIP LOCKED
	LOOP
		BEGIN
			-- Test hook: text '__reject__' forces a controlled promote failure in tests.
			IF r.text = '__reject__' THEN
				RAISE EXCEPTION 'promote reject marker';
			END IF;

			INSERT INTO journal (topic, text, qos, retain, entrypoint, priority)
			VALUES (
				r.topic,
				r.text,
				r.qos,
				CASE WHEN r.retain THEN 1 ELSE 0 END,
				r.entrypoint,
				r.priority
			);

			UPDATE journal_staging
			SET status = 'promoted', promoted_at = NOW()
			WHERE staging_id = r.staging_id;

			v_promoted := v_promoted + 1;

		EXCEPTION WHEN OTHERS THEN
			INSERT INTO journal_rejected (
				staging_id, batch_id, reason, topic, text, qos, retain, entrypoint, priority
			) VALUES (
				r.staging_id,
				r.batch_id,
				SQLERRM,
				r.topic,
				r.text,
				r.qos,
				r.retain,
				r.entrypoint,
				r.priority
			);

			UPDATE journal_staging
			SET status = 'rejected', reject_reason = SQLERRM
			WHERE staging_id = r.staging_id;

			v_rejected := v_rejected + 1;
		END;
	END LOOP;

	RETURN QUERY SELECT v_promoted, v_rejected;
END;
$$;
