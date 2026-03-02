# Testing

## Running the Tests

```bash
poetry run pytest
```

No external services need to be started manually. The test suite uses [testcontainers](https://testcontainers-python.readthedocs.io/) to spin up TimescaleDB and ActiveMQ Artemis automatically at session start.

Docker must be available on the host.

## Infrastructure Fixtures (`conftest.py`)

| Fixture | Scope | Description |
|---------|-------|-------------|
| `db_url` | session | Starts a `timescale/timescaledb:latest-pg16` container and yields its connection URL |
| `artemis_container` | session | Starts an `apache/activemq-artemis:latest-alpine` container and sets `MQTT_BROKER_HOST`/`MQTT_BROKER_PORT` env vars |
| `app` | function | Creates the Quart app, then truncates all data tables and re-seeds the admin user so each test starts with a clean database. Uses the real `asyncpg.Pool` — no transaction wrapping — so concurrent writes (e.g. throughput tests) work without interference. |
| `test_client` | function | Quart test client bound to `app` |
| `execute_graphql` | function | Async helper that POSTs to `/graphql/` and returns parsed JSON |

## Test Files

### `test_graphql.py`

Authentication and topic management: login, logout, token refresh, invalid credentials, token invalidation after logout, topic creation and retrieval.

### `test_alarm.py`

Alarm lifecycle and delivery:

- CRUD via GraphQL (`test_create_alarm`, `test_get_alarms`, `test_update_alarm`)
- Auth enforcement (`test_alarm_unauthorized`)
- End-to-end trigger via MQTT publish (`test_alarm_trigger`)
- Boundary condition — alarm fires at exactly the threshold value (`test_alarm_trigger_at_boundary`)
- Latency characterization — asserts trigger-to-signal time < 500ms (`test_alarm_latency`)
- Webhook delivery — mocks `httpx` and asserts POST URL, headers, and Standard Webhooks–signed payload (`test_alarm_webhook_delivery_standard`)
- Filter/Forward via MQTT — creates an alarm with `deliveryMethod: "mqtt"`, patches `publish_callback` on the alarm instance, publishes a message, and asserts the callback was called with the configured `forwardTopic` and correct payload (`test_alarm_mqtt_forward`)
- Loop prevention — asserts a GraphQL error when `forwardTopic` equals the alarm's input `topic` (`test_alarm_forward_topic_cannot_equal_topic`)

### `test_journal.py`

`getJournalEntries` query coverage:

- Empty result when no entries exist
- Basic row retrieval and text content
- `topic` filter (match and no-match)
- `limit` parameter
- `startTime` filter
- `endTime` filter
- Combined time window (`startTime` + `endTime`)
- Auth enforcement

### `test_integration.py`

Full end-to-end MQTT → journal path: registers a topic via GraphQL, publishes a message to the real Artemis container, waits for the background task to process it, then queries the journal table directly to assert the record was stored.

### `test_throughput.py`

Performance characterization:

| Test | What it measures |
|------|-----------------|
| `test_throughput` | Sequential publish rate (awaited) — default target: 100 msg/s |
| `test_concurrent_throughput` | Concurrent publish rate (semaphore-limited) — default target: 500 msg/s |
| `test_peak_memory` | Publisher-side heap usage over a 3-second window — limit: 100 MiB |

Targets are configurable via environment variables:

```bash
MQTT_TARGET_RATE_SEQUENTIAL=500 MQTT_TARGET_RATE_CONCURRENT=2000 poetry run pytest test/test_throughput.py
```

### `test_websocket.py`

Sanity check for the WebSocket echo server fixture used in other tests.

## Publishing Test Messages

The `LoadCellPublisher` class in `load_cell_example_data.py` simulates a Rice Lake RL20000SS load cell. It can also be run as a standalone CLI tool:

```bash
# Publish continuously at 1-second intervals
poetry run publish-loadcell

# Publish a single message with an explicit weight
poetry run publish-loadcell --mode singular --payload-weight 550.0

# Publish N messages concurrently
poetry run publish-loadcell --mode n_messages --count 100
```
