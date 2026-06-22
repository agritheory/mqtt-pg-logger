# MQTT Logger

Subscribes to MQTT topics, stores messages in TimescaleDB, and exposes a GraphQL API for configuration, querying, and alarm management.

Depends on Apache ActiveMQ Artemis and TimescaleDB.

Running `docker compose up --build` starts three services:
- A TimescaleDB container
- An ActiveMQ Artemis container
- A Python/Quart container running the application

## Configuration

### Application

```env
SECRET_KEY=yohjohthieNguvayaiFaeph5Oomae9nu
FERNET_KEY=D7jNgKGahOrZtQVd9reaT53B4SAz-gLH2ZJtRStpCsY=
JWT_SECRET_KEY=59eggsXI2uU2eaWmmOcr_zMKfhE4rW-h0avo0IKNJS4
HOST=0.0.0.0
PORT=5000
ACCESS_TOKEN_EXPIRES=3600
REFRESH_TOKEN_EXPIRES=2592000
CORS_ORIGINS=["*"]
```

### TimescaleDB

```env
DB_HOST=timescaledb
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASSWORD=postgres
```

### Admin Account

```env
ADMIN_EMAIL=admin@agritheory.dev
ADMIN_PASSWORD=ohch4GeiSie
```

### MQTT Broker (ActiveMQ Artemis)

```env
MQTT_BROKER_HOST=artemis
MQTT_BROKER_PORT=1883
MQTT_BROKER_WEB_CONSOLE_PORT=8161
MQTT_USER=artemis
MQTT_PASSWORD=artemis
SSL_INSECURE=True
MQTT_CLIENT_ID=mqtt-logger
MQTT_KEEPALIVE=60
MQTT_DEFAULT_PROTOCOL=5   # 5=MQTTv5, 4=MQTTv311, 3=MQTTv31
MQTT_DEFAULT_QUALITY=1
```

### Topic Filtering

```env
LOG_ALL_TOPICS=false      # Auto-register topics seen on the broker
ALLOW_ALL_TOPICS=false    # Store messages for all topics, not just registered ones
```

### Production

```env
DOMAIN=''
SSL_CA_CERTS=''
SSL_CERTFILE=''
SSL_KEYFILE=''
```

---

## GraphQL API

The GraphiQL IDE is available at `http://localhost:5000/graphql/` in development.

All queries and mutations except `login` require a Bearer token in the `Authorization` header.

### Authentication

#### Login

```graphql
mutation Login($username: String!, $password: String!) {
	login(input: { username: $username, password: $password }) {
		message
		accessToken
		refreshToken
		tokenType
		expiresIn
	}
}
```

Use the returned `accessToken` as a Bearer token:

```json
{ "Authorization": "Bearer <accessToken>" }
```

#### Refresh Token

```graphql
mutation RefreshToken($refreshToken: String!) {
	refreshToken(input: { refreshToken: $refreshToken }) {
		accessToken
		refreshToken
		expiresIn
	}
}
```

#### Logout

```graphql
mutation {
	logout
}
```

---

### Topics

Topics control which MQTT subjects the logger subscribes to and stores.

```graphql
mutation {
	createTopic(input: { topic: "sensors/loadcell/#" }) {
		id
		topic
	}
}
```

```graphql
query {
	getTopics {
		id
		topic
		disabled
		creation
		modified
	}
}
```

---

### Journal

The journal stores every received MQTT message. Entries are queryable with optional filters.

```graphql
query GetJournalEntries(
	$topic: String
	$startTime: DateTime
	$endTime: DateTime
	$limit: Int
) {
	getJournalEntries(
		topic: $topic
		startTime: $startTime
		endTime: $endTime
		limit: $limit
	) {
		id
		topic
		text
		data
		creation
	}
}
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `topic` | String | Exact topic match |
| `startTime` | DateTime | Inclusive lower bound on `creation` (ISO 8601) |
| `endTime` | DateTime | Inclusive upper bound on `creation` (ISO 8601) |
| `limit` | Int | Maximum results (default 100, max 1000) |

Results are ordered by `creation DESC`.

**Example — last 50 readings from a sensor:**

```graphql
query {
	getJournalEntries(topic: "sensors/loadcell/RL20000SS-500LB/data" limit: 50) {
		id
		text
		creation
	}
}
```

**Example — time-windowed query:**

```graphql
query {
	getJournalEntries(
		topic: "sensors/temperature/room1"
		startTime: "2025-01-15T00:00:00Z"
		endTime: "2025-01-15T23:59:59Z"
	) {
		id
		data
		creation
	}
}
```

---

### Alarms and Webhooks

See [alarms.md](./alarms.md) for the full alarm and webhook API reference.

**Quick reference — webhook delivery:**

```graphql
# Create a webhook endpoint
mutation {
	createWebhook(input: {
		name: "SCADA Receiver"
		url: "https://erp.example.com/api/method/scada.api.receive_alarm"
	}) {
		id
		signingSecret   # store this — not recoverable
	}
}

# Create an alarm that POSTs to the webhook on trigger
mutation {
	alarm(input: {
		condition: "message['temperature'] > 75"
		owner: "admin@agritheory.dev"
		modifiedBy: "admin@agritheory.dev"
		topic: "sensors/temperature/room1"
		alarmName: "Overheat Warning"
		deliveryMethod: "webhook"
		webhookId: 1
	}) {
		id
		alarmName
	}
}
```

**Quick reference — MQTT filter/forward:**

```graphql
# Republish matching messages to a different topic
mutation {
	alarm(input: {
		condition: "message['temperature'] > 75"
		owner: "admin@agritheory.dev"
		modifiedBy: "admin@agritheory.dev"
		topic: "sensors/temperature/room1"
		alarmName: "Overheat — Forward to SCADA"
		deliveryMethod: "mqtt"
		forwardTopic: "scada/alarms/overheat"
	}) {
		id
		alarmName
		forwardTopic
	}
}
```

---

### Health Check

```graphql
query {
	health {
		status
		timestamp
		mqttConnection
		timescaledbStatus
		artemisStatus
	}
}
```

---

## Error Format

All errors follow the GraphQL specification:

```json
{
  "errors": [
    { "message": "Authorization required" }
  ]
}
```

When a query partially succeeds, both `data` and `errors` are present.

---

## Database Migrations

Schema changes are applied automatically at startup via numbered SQL files in the [`migrations/`](../migrations/) directory. Add a new file such as `003_description.sql` for each schema change; the runner records applied versions in `schema_migrations`.

---

## Generating Cryptographic Keys

```bash
# Fernet key (for encrypting stored passwords)
poetry run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# JWT secret (any sufficiently random string)
poetry run python -c "import secrets; print(secrets.token_urlsafe(32))"
```
