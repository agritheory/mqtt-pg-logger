-- The content of this file is parsed into commands by a quite simple algorithm. So please don't use ";" in comments


-- DROP TABLE pgqueuer;  -- do it manually. the automatic creation will abort/fail here.

CREATE TYPE pgqueuer_status AS ENUM ('queued', 'picked');

CREATE TABLE pgqueuer (
    pgqueuer_id SERIAL PRIMARY KEY,
    id INTEGER NOT NULL,
    topic VARCHAR(256),
    text VARCHAR(4096),
    data JSONB,
    message_id INTEGER,
    qos INTEGER,
    retain INTEGER,
    priority INT NOT NULL,
	created TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
	updated TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
	status pgqueuer_status NOT NULL,
	entrypoint TEXT NOT NULL,
    payload VARCHAR(4096) GENERATED ALWAYS AS (text) STORED,
    time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);


COMMENT ON COLUMN pgqueuer.pgqueuer_id is 'Primary key';
COMMENT ON COLUMN pgqueuer.message_id is 'Client message id (mid).';
COMMENT ON COLUMN pgqueuer.text is 'Message payload as standard text';
COMMENT ON COLUMN pgqueuer.data is 'JSON representation (generated out of "text" if not explicitly provided)';
COMMENT ON COLUMN pgqueuer.qos is 'Message quality of service 0, 1 or 2.';
COMMENT ON COLUMN pgqueuer.retain is 'If 1, the message is a retained message.';
COMMENT ON COLUMN pgqueuer.topic is 'Message topic.';
COMMENT ON COLUMN pgqueuer.time is 'Message or insert time';
COMMENT ON COLUMN pgqueuer.priority is 'Priority of the job, higher value means higher priority.';
COMMENT ON COLUMN pgqueuer.created is 'Timestamp when the job was created.';
COMMENT ON COLUMN pgqueuer.updated is 'Timestamp when the job was last updated.';
COMMENT ON COLUMN pgqueuer.status is 'Status of the job (queued, picked).';
COMMENT ON COLUMN pgqueuer.entrypoint is 'The entrypoint function that will process the job.';

-- used for regular clean up
CREATE INDEX CONCURRENTLY pgqueuer_time_idx ON pgqueuer ( time );

CREATE INDEX CONCURRENTLY pgqueuer_name_idx ON pgqueuer ( topic );

CREATE TYPE pgqueuer_statistics_status AS ENUM ('exception', 'successful');

CREATE TABLE pgqueuer_statistics (
	id SERIAL PRIMARY KEY,               -- Unique identifier for each log entry.
	created TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT DATE_TRUNC('sec', NOW() at time zone 'UTC'), -- Timestamp when the log entry was created.
	count BIGINT NOT NULL,               -- Number of jobs processed.
	priority INT NOT NULL,               -- Priority of the jobs being logged.
	time_in_queue INTERVAL NOT NULL,     -- Time the job spent in the queue.
	status pgqueuer_statistics_status NOT NULL, -- Status of the job processing (exception, successful).
	entrypoint TEXT NOT NULL             -- The entrypoint function that processed the job.
);

CREATE TABLE journal (
    message_id SERIAL PRIMARY KEY,
    topic TEXT NOT NULL,
    text TEXT NOT NULL,
    qos INT,
    retain BOOLEAN,
    time TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- manual test
-- INSERT INTO pgqueuer (message_id, topic, text, qos, retain) values (1, 'topic', '{"a": "json"}', 1, 0);
-- SELECT * FROM pgqueuer;
