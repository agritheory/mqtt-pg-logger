# Alarms

The Alarm system monitors MQTT messages and triggers notifications when user-defined conditions are met. Alarms use RestrictedPython to safely evaluate conditions against incoming message data, providing a flexible way to monitor sensor data, system states, and other metrics.

- **Safe Condition Evaluation**: Uses RestrictedPython to execute user-defined conditions securely
- **Topic-Based Filtering**: Alarms only evaluate messages from their configured topic
- **[PID Controller](https://en.wikipedia.org/wiki/Proportional%E2%80%93integral%E2%80%93derivative_controller) Integration**: Built-in PID control functions for advanced monitoring
- **Real-Time Processing**: Conditions are evaluated as messages arrive
- **GraphQL API**: Create, update, and manage alarms via GraphQL

## Architecture

```
MQTT Message → Topic Filter → Condition Evaluation → Alarm Triggered → Signal Emission
```

When an MQTT message arrives:
1. The system checks if any alarms are subscribed to that topic
2. For each matching alarm, the condition is evaluated with the message data
3. If the condition returns `True`, the alarm is triggered
4. A signal is emitted with the alarm details and message data
5. Notification handlers can respond to the signal

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
| `deliveryMethod` | String | Yes | Notification method (e.g., "email") |
| `disabled` | Boolean | No | Whether alarm is disabled (default: false) |
| `id` | Integer | No | For updates only; omit for new alarms |

## Writing Conditions

### Available Variables

When a condition is evaluated, the following variables are available:

- `message`: The decoded MQTT message payload (dict or parsed JSON)
- `pid_compute()`: Function to compute PID controller output
- `pid_reset()`: Function to reset a PID controller
- `pid_last_output()`: Function to get last PID output

### Condition Syntax

Conditions are Python expressions that must return a boolean value. They have access to the `message` variable containing the MQTT payload.

#### Simple Conditions

```python
# Temperature threshold
message['temperature'] > 75

# Check nested values
message['measurement']['weight']['value'] > 500

# Multiple conditions
message['temperature'] > 75 and message['humidity'] < 30

# String matching
message['status'] == 'critical'
```

#### Using PID Controllers

```python
# Compute PID and check if output exceeds threshold
pid_compute('heater/zone1', 72.0, message['temperature'], kp=1.0, ki=0.1, kd=0.05) > 50

# Check if PID output is within range
abs(pid_last_output('mixer/speed')) < 10
```

### PID Functions

#### `pid_compute()`

Computes PID controller output and updates internal state.

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

#### `pid_reset()`

Resets a PID controller to initial state.

```python
pid_reset('heater/zone1')
```

#### `pid_last_output()`

Retrieves the last computed output without recalculating.

```python
pid_last_output('heater/zone1')
```

## Examples

### Temperature Monitoring

```graphql
mutation {
	alarm(input: {
		condition: "message['temperature'] > 75"
		owner: "admin@agritheory.dev"
		modifiedBy: "admin@agritheory.dev"
		topic: "sensors/temperature/room1"
		alarmName: "Overheat Warning"
		deliveryMethod: "email"
		disabled: false
	}) {
		id
		alarmName
	}
}
```

### Load Cell Monitoring

```graphql
mutation {
	alarm(input: {
		condition: "message['measurement']['weight']['value'] >= 500"
		owner: "admin@agritheory.dev"
		modifiedBy: "admin@agritheory.dev"
		topic: "sensors/loadcell/RL20000SS-500LB/data"
		alarmName: "Load Cell Overload"
		deliveryMethod: "email"
		disabled: false
	}) {
		id
		alarmName
	}
}
```

### PID-Based Temperature Control

```graphql
mutation {
	alarm(input: {
		condition: "pid_compute('furnace/main', 1500, message['temperature'], kp=2.0, ki=0.5, kd=0.1, max_output=100) < 10"
		owner: "admin@agritheory.dev"
		modifiedBy: "admin@agritheory.dev"
		topic: "sensors/furnace/temperature"
		alarmName: "Furnace Underheat"
		deliveryMethod: "email"
		disabled: false
	}) {
		id
		alarmName
	}
}
```

## Managing Alarms

### Retrieving Alarms

```graphql
query {
	getAlarms {
		id
		condition
		topic
		alarmName
		deliveryMethod
		disabled
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
	}
}
```

### Updating Alarms

To update an alarm, include the `id` field in your mutation:

```graphql
mutation {
	alarm(input: {
		id: 1
		condition: "message['temperature'] > 80"  # Updated threshold
		owner: "admin@agritheory.dev"
		modifiedBy: "admin@agritheory.dev"
		topic: "sensors/temperature/room1"
		alarmName: "Overheat Warning"
		deliveryMethod: "email"
		disabled: false
	}) {
		id
		condition
		modified
	}
}
```

### Disabling Alarms

```graphql
mutation {
	alarm(input: {
		id: 1
		condition: "message['temperature'] > 75"
		owner: "admin@agritheory.dev"
		modifiedBy: "admin@agritheory.dev"
		topic: "sensors/temperature/room1"
		alarmName: "Overheat Warning"
		deliveryMethod: "email"
		disabled: true  # Disable the alarm
	}) {
		id
		disabled
	}
}
```

## Signal Handling

When an alarm is triggered, it emits the `alarm_triggered` signal with the following data:

```python
from src.signals import alarm_triggered

def handle_alarm(sender, **kwargs):
	alarm = kwargs['alarm']          # CompiledAlarm object
	message_data = kwargs['message_data']  # Dict with message payload

	print(f"Alarm triggered: {alarm.alarm_name}")
	print(f"Condition: {alarm.condition}")
	print(f"Message: {message_data}")

alarm_triggered.connect(handle_alarm)
```

### Custom Notification Handlers

You can implement custom notification logic by connecting to the signal:

```python
from src.signals import alarm_triggered
import asyncio

def email_notification(sender, **kwargs):
	alarm = kwargs['alarm']
	if alarm.delivery_method == 'email':
		# Send email notification
		asyncio.create_task(send_email(
			to=alarm.owner,
			subject=f"Alarm: {alarm.alarm_name}",
			body=f"Condition met: {alarm.condition}"
		))

alarm_triggered.connect(email_notification)
```