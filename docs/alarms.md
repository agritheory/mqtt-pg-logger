# Alarms

The alarm system monitors MQTT messages and triggers notifications when user-defined conditions are met. Conditions are evaluated using RestrictedPython, which allows flexible Python expressions while preventing unsafe operations.

- **Safe Condition Evaluation**: Uses RestrictedPython to execute user-defined conditions securely
- **Topic-Based Filtering**: Alarms only evaluate messages from their configured topic
- **[PID Controller](https://en.wikipedia.org/wiki/Proportional%E2%80%93integral%E2%80%93derivative_controller) Integration**: Built-in PID control functions for advanced monitoring
- **Real-Time Processing**: Conditions are evaluated as messages arrive
- **Multiple Delivery Methods**: Webhook (Standard Webhooks signed HTTP POST) or MQTT Filter/Forward (republish to a new topic)
- **GraphQL API**: Create, update, and manage alarms and webhooks via GraphQL

## Architecture

```
MQTT Message → Topic Filter → Condition Evaluation → Alarm Triggered → Delivery
                                                                     ├─ webhook  → Signed HTTP POST
                                                                     └─ mqtt     → Republish to forwardTopic
```

When an MQTT message arrives:
1. The system checks if any alarms are subscribed to that topic
2. For each matching alarm, the condition is evaluated against the message payload
3. If the condition returns `True`, the alarm fires
4. A Blinker signal (`alarm_triggered`) is emitted with the alarm object and message data
5. Delivery is dispatched based on `deliveryMethod`:
   - `"webhook"` — a signed HTTP POST is sent to the configured webhook endpoint
   - `"mqtt"` — the message payload is republished to the configured `forwardTopic`

## Webhooks

Webhooks are managed as a separate resource and referenced by alarms. This means a single webhook endpoint can be shared across multiple alarms, and credentials are managed independently.

Webhook delivery follows the [Standard Webhooks](https://www.standardwebhooks.com/) specification: each request carries a HMAC-SHA256 signature so the receiver can verify authenticity.

### Creating a Webhook

```graphql
mutation {
	createWebhook(input: {
		name: "SCADA Alarm Receiver"
		url: "https://your-erp-instance/api/method/scada.api.receive_alarm"
		disabled: false
	}) {
		id
		name
		url
		signingSecret
	}
}
```

Store the `signingSecret` returned from this mutation — it is the `whsec_<base64>` key used to verify incoming requests. It is not recoverable after creation.

### Verifying Webhook Requests

Every delivery includes three headers:

| Header | Description |
|--------|-------------|
| `webhook-id` | Unique message ID (`msg_<random>`) |
| `webhook-timestamp` | Unix timestamp (seconds) of delivery |
| `webhook-signature` | `v1,<base64-hmac-sha256>` over `msg_id.timestamp.payload` |

Use the [Standard Webhooks SDK](https://github.com/standard-webhooks/standard-webhooks) for your language to verify:

```python
from standardwebhooks import Webhook

wh = Webhook("whsec_<your-signing-secret>")
payload = wh.verify(raw_body, headers)  # raises on invalid signature
```

### Webhook Payload Format

All alarm deliveries use the `alarm.triggered` event type:

```json
{
  "type": "alarm.triggered",
  "timestamp": "2025-01-15T14:32:00.123456+00:00",
  "data": {
    "alarm_name": "Load Cell Overload",
    "topic": "sensors/loadcell/RL20000SS-500LB/data",
    "condition": "message['measurement']['weight']['value'] >= 500",
    "message_data": { ... }
  }
}
```

### Listing Webhooks

```graphql
query {
	getWebhooks {
		id
		name
		url
		disabled
		creation
	}
}
```

---

## Creating Alarms

### GraphQL Mutation

```graphql
mutation CreateOrUpdateAlarm($input: AlarmInput!) {
	alarm(input: $input) {
		id
		condition
		topic
		alarmName
		deliveryMethod
		disabled
		webhookId
	}
}
```

### Input Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `condition` | String | Yes | Python expression that returns boolean |
| `owner` | String | Yes | Username of the alarm owner |
| `modifiedBy` | String | Yes | Username of last modifier |
| `topic` | String | Yes | MQTT topic to monitor |
| `alarmName` | String | Yes | Human-readable name for the alarm |
| `deliveryMethod` | String | Yes | `"webhook"` or `"mqtt"` |
| `disabled` | Boolean | No | Whether alarm is disabled (default: false) |
| `id` | Integer | No | For updates only; omit to create a new alarm |
| `webhookId` | Integer | No | Required when `deliveryMethod` is `"webhook"` |
| `forwardTopic` | String | No | Required when `deliveryMethod` is `"mqtt"`; must differ from `topic` |

---

## Writing Conditions

Conditions are Python expressions evaluated against the incoming MQTT message payload. They must return a boolean.

### Available Variables

| Variable | Description |
|----------|-------------|
| `message` | Decoded MQTT payload (dict if JSON, string otherwise) |
| `pid_compute()` | Compute PID controller output |
| `pid_reset()` | Reset a PID controller to initial state |
| `pid_last_output()` | Retrieve last PID output without recalculating |

### Simple Conditions

```python
# Temperature threshold
message['temperature'] > 75

# Nested value
message['measurement']['weight']['value'] >= 500

# Multiple conditions
message['temperature'] > 75 and message['humidity'] < 30

# String match
message['status'] == 'critical'
```

### Using PID Controllers

```python
# Fire when PID output exceeds a threshold
pid_compute('heater/zone1', 72.0, message['temperature'], kp=1.0, ki=0.1, kd=0.05) > 50

# Fire when error is within acceptable range
abs(pid_last_output('mixer/speed')) < 10
```

### PID Functions

#### `pid_compute()`

```python
pid_compute(
	pid_id,          # Unique controller ID (string)
	setpoint,        # Target value (float)
	process_value,   # Current measured value (float)
	kp=1.0,          # Proportional gain
	ki=0.0,          # Integral gain
	kd=0.0,          # Derivative gain
	min_output=-inf, # Minimum output limit
	max_output=inf   # Maximum output limit
)
```

#### `pid_reset(pid_id)`

Resets a PID controller to its initial state.

#### `pid_last_output(pid_id)`

Retrieves the last computed output without recalculating.

---

## Examples

### Temperature Alarm with Webhook

```graphql
mutation {
	alarm(input: {
		condition: "message['temperature'] > 75"
		owner: "admin@agritheory.dev"
		modifiedBy: "admin@agritheory.dev"
		topic: "sensors/temperature/room1"
		alarmName: "Overheat Warning"
		deliveryMethod: "webhook"
		webhookId: 1
		disabled: false
	}) {
		id
		alarmName
		webhookId
	}
}
```

### Load Cell Threshold

```graphql
mutation {
	alarm(input: {
		condition: "message['measurement']['weight']['value'] >= 500"
		owner: "admin@agritheory.dev"
		modifiedBy: "admin@agritheory.dev"
		topic: "sensors/loadcell/RL20000SS-500LB/data"
		alarmName: "Load Cell Overload"
		deliveryMethod: "webhook"
		webhookId: 1
		disabled: false
	}) {
		id
		alarmName
	}
}
```

### PID-Based Control

```graphql
mutation {
	alarm(input: {
		condition: "pid_compute('furnace/main', 1500, message['temperature'], kp=2.0, ki=0.5, kd=0.1, max_output=100) < 10"
		owner: "admin@agritheory.dev"
		modifiedBy: "admin@agritheory.dev"
		topic: "sensors/furnace/temperature"
		alarmName: "Furnace Underheat"
		deliveryMethod: "webhook"
		webhookId: 1
		disabled: false
	}) {
		id
		alarmName
	}
}
```

---

## Filter / Forward via MQTT

Instead of delivering to an HTTP endpoint, an alarm can republish the triggering message to a different MQTT topic. This is useful for routing or fan-out: downstream subscribers can consume the forwarded topic without needing to evaluate the condition themselves.

Set `deliveryMethod` to `"mqtt"` and provide a `forwardTopic`. The `forwardTopic` must differ from the alarm's `topic` — identical values are rejected to prevent infinite message loops.

```graphql
mutation {
	alarm(input: {
		condition: "message['measurement']['weight']['value'] >= 500"
		owner: "admin@agritheory.dev"
		modifiedBy: "admin@agritheory.dev"
		topic: "sensors/loadcell/RL20000SS-500LB/data"
		alarmName: "Overload — Forward to SCADA"
		deliveryMethod: "mqtt"
		forwardTopic: "scada/alarms/overload"
	}) {
		id
		alarmName
		deliveryMethod
		forwardTopic
	}
}
```

The forwarded payload is the original message serialised as JSON, identical to what was received on `topic`. The MQTT broker and all its existing subscribers are used — no separate connection is created.

---

## Managing Alarms

### Retrieving All Alarms

```graphql
query {
	getAlarms {
		id
		condition
		topic
		alarmName
		deliveryMethod
		disabled
		webhookId
		owner
		creation
		modified
	}
}
```

### Filtering Alarms

```graphql
query {
	getAlarms(
		owner: "admin@agritheory.dev"
		topic: "sensors/temperature/room1"
		disabled: false
	) {
		id
		alarmName
		webhookId
	}
}
```

### Updating an Alarm

Include the `id` field to update an existing alarm:

```graphql
mutation {
	alarm(input: {
		id: 1
		condition: "message['temperature'] > 80"
		owner: "admin@agritheory.dev"
		modifiedBy: "admin@agritheory.dev"
		topic: "sensors/temperature/room1"
		alarmName: "Overheat Warning"
		deliveryMethod: "webhook"
		webhookId: 1
		disabled: false
	}) {
		id
		condition
		modified
	}
}
```

### Disabling an Alarm

```graphql
mutation {
	alarm(input: {
		id: 1
		condition: "message['temperature'] > 75"
		owner: "admin@agritheory.dev"
		modifiedBy: "admin@agritheory.dev"
		topic: "sensors/temperature/room1"
		alarmName: "Overheat Warning"
		deliveryMethod: "webhook"
		webhookId: 1
		disabled: true
	}) {
		id
		disabled
	}
}
```

---

## Signal Handling

In addition to webhook delivery, the `alarm_triggered` Blinker signal is emitted on every alarm trigger. This is useful for in-process subscribers:

```python
from src.signals import alarm_triggered

def handle_alarm(sender, **kwargs):
	alarm = kwargs['alarm']           # CompiledAlarm dataclass
	message_data = kwargs['message_data']  # dict

	print(f"Alarm: {alarm.alarm_name}")
	print(f"Condition: {alarm.condition}")
	print(f"Data: {message_data}")

alarm_triggered.connect(handle_alarm)
```
