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
    priority INTEGER NOT NULL,
    created TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    status pgqueuer_status NOT NULL,
    entrypoint TEXT NOT NULL,
    payload VARCHAR(4096) GENERATED ALWAYS AS (text) STORED,
    time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- used for regular clean up
CREATE TYPE pgqueuer_statistics_status AS ENUM ('exception', 'successful');

CREATE TABLE pgqueuer_statistics (
    id SERIAL PRIMARY KEY,               -- Unique identifier for each log entry.
    created TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT DATE_TRUNC('sec', NOW() at time zone 'UTC'), -- Timestamp when the log entry was created.
    count BIGINT NOT NULL,               -- Number of jobs processed.
    priority INTEGER NOT NULL,               -- Priority of the jobs being logged.
    time_in_queue INTERVAL NOT NULL,     -- Time the job spent in the queue.
    status pgqueuer_statistics_status NOT NULL, -- Status of the job processing (exception, successful).
    entrypoint TEXT NOT NULL             -- The entrypoint function that processed the job.
);

CREATE TABLE journal (
    message_id SERIAL PRIMARY KEY,
    topic TEXT NOT NULL,
    text TEXT NOT NULL,
    qos INTEGER,
    retain INTEGER,
    time TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
