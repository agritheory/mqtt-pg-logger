import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from test.load_cell_example_data import LoadCellPublisher
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from quart.testing import QuartClient

from src.alarm import CompiledAlarm

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------------
# GraphQL documents
# --------------------------------------------------------------------------------

ALARM_MUTATION = """
mutation CreateOrUpdateAlarm($input: AlarmInput!) {
  alarm(input: $input) {
    id
    condition
    topic
    alarmName
    deliveryMethod
    disabled
    webhookId
    forwardTopic
  }
}
"""

CREATE_WEBHOOK_MUTATION = """
mutation CreateWebhook($input: WebhookInput!) {
  createWebhook(input: $input) {
    id
    name
    url
    signingSecret
    disabled
  }
}
"""

GET_ALARMS_QUERY = """
query {
  getAlarms {
    id
    condition
    topic
    alarmName
    deliveryMethod
    disabled
  }
}
"""

# --------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------


@pytest.fixture  # type: ignore[misc]
def new_alarm_input() -> dict[str, Any]:
	return {
		"condition": "message['temperature'] > 75",
		"owner": "admin@agritheory.dev",
		"modifiedBy": "admin@agritheory.dev",
		"topic": "overheat",
		"alarmName": "Overheat Warning",
		"deliveryMethod": "email",
		"disabled": False,
	}


@pytest.fixture  # type: ignore[misc]
def new_alarm_load_cell() -> dict[str, Any]:
	return {
		"condition": "message['measurement']['weight']['value'] > 0",
		"owner": "admin@agritheory.dev",
		"modifiedBy": "admin@agritheory.dev",
		"disabled": False,
		"topic": "sensors/loadcell/RL20000SS-500LB/data",
		"alarmName": "Sensor Activated",
		"deliveryMethod": "email",
	}


# --------------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------------


@pytest.mark.asyncio  # type: ignore[misc]
async def test_create_alarm(
	test_client: QuartClient,
	login_mutation: str,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
	new_alarm_input: dict[str, Any],
) -> None:
	# Authenticate
	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	# Create a new alarm
	resp = await execute_graphql(
		ALARM_MUTATION,
		token=token,
		variables={"input": new_alarm_input},
	)
	assert "data" in resp and "alarm" in resp["data"]
	alarm = resp["data"]["alarm"]

	# Basic sanity checks
	assert alarm["id"] is not None
	assert alarm["condition"] == new_alarm_input["condition"]
	assert alarm["topic"] == new_alarm_input["topic"]
	assert alarm["disabled"] is False


@pytest.mark.asyncio  # type: ignore[misc]
async def test_get_alarms(
	test_client: QuartClient,
	login_mutation: str,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
	new_alarm_input: dict[str, Any],
) -> None:
	# Authenticate
	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	# Create an alarm first so the list is non-empty
	await execute_graphql(ALARM_MUTATION, token=token, variables={"input": new_alarm_input})

	# Retrieve the list of alarms
	resp = await execute_graphql(
		GET_ALARMS_QUERY,
		token=token,
	)
	assert "data" in resp and "getAlarms" in resp["data"]
	alarms = resp["data"]["getAlarms"]

	assert isinstance(alarms, list)
	assert len(alarms) > 0


@pytest.mark.asyncio  # type: ignore[misc]
async def test_update_alarm(
	test_client: QuartClient,
	login_mutation: str,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
	new_alarm_input: dict[str, Any],
) -> None:
	# Authenticate
	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	# Create a fresh alarm to update
	create_resp = await execute_graphql(
		ALARM_MUTATION,
		token=token,
		variables={"input": new_alarm_input},
	)
	alarm = create_resp["data"]["alarm"]
	alarm_id = alarm["id"]

	# Modify fields
	updated_input = new_alarm_input.copy()
	updated_input["id"] = int(alarm_id)
	updated_input["condition"] = "message['temperature'] > 80"
	updated_input["disabled"] = True

	# Perform the update
	update_resp = await execute_graphql(
		ALARM_MUTATION,
		token=token,
		variables={"input": updated_input},
	)
	updated_alarm = update_resp["data"]["alarm"]

	assert updated_alarm["id"] == alarm_id
	assert updated_alarm["condition"] == updated_input["condition"]
	assert updated_alarm["disabled"] is True


@pytest.mark.asyncio  # type: ignore[misc]
async def test_alarm_unauthorized(
	test_client: QuartClient,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
	new_alarm_input: dict[str, Any],
) -> None:
	# Attempt to create without a token
	resp = await execute_graphql(
		ALARM_MUTATION,
		token=None,
		variables={"input": new_alarm_input},
	)
	assert "errors" in resp
	assert "Authorization required" in resp["errors"][0]["message"]


@pytest.mark.asyncio  # type: ignore[misc]
async def test_alarm_trigger(
	test_client: QuartClient,
	login_mutation: str,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
	new_alarm_load_cell: dict[str, Any],
) -> None:
	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	resp = await execute_graphql(
		ALARM_MUTATION,
		token=token,
		variables={"input": new_alarm_load_cell},
	)
	alarm_id = resp["data"]["alarm"]["id"]
	received: dict[str, Any] = {}
	event = asyncio.Event()

	def receiver(sender: Any, **kwargs: Any) -> None:
		received.update(kwargs)
		event.set()

	from src.signals import alarm_triggered

	alarm_triggered.connect(receiver)

	await LoadCellPublisher().publish_n_messages(1)

	try:
		await asyncio.wait_for(event.wait(), timeout=2.0)
	finally:
		alarm_triggered.disconnect(receiver)

	logger.info("Received: %s", received)
	assert "alarm" in received
	assert "message_data" in received

	alarm_obj = received["alarm"]
	assert isinstance(alarm_obj, CompiledAlarm)
	assert alarm_obj.id == int(alarm_id)
	assert alarm_obj.topic == new_alarm_load_cell["topic"]
	assert "weight" in alarm_obj.condition

	msg = received["message_data"]
	assert isinstance(msg, dict)
	assert "measurement" in msg and "weight" in msg["measurement"]
	weight = msg["measurement"]["weight"]["value"]
	assert isinstance(weight, float)
	assert weight > 0.0


@pytest.mark.asyncio  # type: ignore[misc]
async def test_alarm_latency(
	test_client: QuartClient,
	login_mutation: str,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
	new_alarm_load_cell: dict[str, Any],
) -> None:
	# --- setup and login ---
	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]
	resp = await execute_graphql(
		ALARM_MUTATION,
		token=token,
		variables={"input": new_alarm_load_cell},
	)
	alarm_id = resp["data"]["alarm"]["id"]

	# Prepare to capture signal and latency
	received: dict[str, Any] = {}
	event = asyncio.Event()

	def receiver(sender: Any, **kwargs: Any) -> None:
		# record when signal arrives and payload
		received["timestamp"] = time.monotonic()
		received["kwargs"] = kwargs
		event.set()

	from src.signals import alarm_triggered

	alarm_triggered.connect(receiver)

	# mark start time, publish, and wait for signal
	start_ts = time.monotonic()
	await LoadCellPublisher().publish_n_messages(1)

	try:
		await asyncio.wait_for(event.wait(), timeout=1.0)
	finally:
		alarm_triggered.disconnect(receiver)

	# compute latency
	end_ts = received["timestamp"]
	latency = end_ts - start_ts
	# assert latency threshold
	logger.info(f"Latency: {latency:.3f}s")
	assert latency < 0.5, f"Latency too high: {latency:.3f}s"

	# basic payload sanity checks (optional, can mirror test_alarm_trigger)
	payload = received["kwargs"]
	assert "alarm" in payload
	assert "message_data" in payload


@pytest.mark.asyncio  # type: ignore[misc]
async def test_alarm_trigger_at_boundary(
	test_client: QuartClient,
	login_mutation: str,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
	new_alarm_load_cell: dict[str, Any],
) -> None:
	"""Alarm fires when the message value is exactly at the condition threshold (>= 500)."""
	new_alarm_load_cell["condition"] = "message['measurement']['weight']['value'] >= 500"
	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	resp = await execute_graphql(
		ALARM_MUTATION,
		token=token,
		variables={"input": new_alarm_load_cell},
	)
	alarm_id = resp["data"]["alarm"]["id"]
	received: dict[str, Any] = {}
	event = asyncio.Event()

	def receiver(sender: Any, **kwargs: Any) -> None:
		received.update(kwargs)
		event.set()

	from src.signals import alarm_triggered

	alarm_triggered.connect(receiver)

	await LoadCellPublisher().publish_n_messages(1, payload_weight=500.0)

	try:
		await asyncio.wait_for(event.wait(), timeout=2.0)
	finally:
		alarm_triggered.disconnect(receiver)

	logger.info("Received: %s", received)
	assert "alarm" in received
	assert "message_data" in received

	alarm_obj = received["alarm"]
	assert isinstance(alarm_obj, CompiledAlarm)
	assert alarm_obj.id == int(alarm_id)
	assert alarm_obj.topic == new_alarm_load_cell["topic"]
	assert "weight" in alarm_obj.condition

	msg = received["message_data"]
	assert isinstance(msg, dict)
	assert "measurement" in msg and "weight" in msg["measurement"]
	weight = msg["measurement"]["weight"]["value"]
	assert isinstance(weight, float)
	assert weight == 500.0


@pytest.mark.asyncio  # type: ignore[misc]
async def test_create_webhook(
	test_client: QuartClient,
	login_mutation: str,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
) -> None:
	"""createWebhook creates a webhook with generated signing secret."""
	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	webhook_input = {
		"name": "Test Webhook",
		"url": "https://example.com/webhook",
		"disabled": False,
	}
	resp = await execute_graphql(
		CREATE_WEBHOOK_MUTATION,
		token=token,
		variables={"input": webhook_input},
	)
	assert "data" in resp and "createWebhook" in resp["data"]
	webhook = resp["data"]["createWebhook"]
	assert webhook["id"] is not None
	assert webhook["name"] == webhook_input["name"]
	assert webhook["url"] == webhook_input["url"]
	assert webhook["signingSecret"].startswith("whsec_")
	assert webhook["disabled"] is False


GET_WEBHOOK_QUERY = """
query GetWebhook($id: Int!) {
  webhook(id: $id) {
    id
    name
    url
    signingSecret
    disabled
  }
}
"""


@pytest.mark.asyncio  # type: ignore[misc]
async def test_fetch_webhook_secret_via_graphql(
	test_client: QuartClient,
	login_mutation: str,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
) -> None:
	"""Authenticated webhook(id) query returns signingSecret for receiver verification."""
	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	# Create webhook
	create_resp = await execute_graphql(
		CREATE_WEBHOOK_MUTATION,
		token=token,
		variables={
			"input": {"name": "SCADA Alarm", "url": "https://scada.example.com/webhook", "disabled": False}
		},
	)
	webhook_id = create_resp["data"]["createWebhook"]["id"]
	created_secret = create_resp["data"]["createWebhook"]["signingSecret"]

	# Fetch via webhook(id) - secret available for receiver config
	fetch_resp = await execute_graphql(GET_WEBHOOK_QUERY, token=token, variables={"id": webhook_id})
	fetched = fetch_resp["data"]["webhook"]
	assert fetched["id"] == webhook_id
	assert fetched["signingSecret"] == created_secret
	assert fetched["signingSecret"].startswith("whsec_")


@pytest.mark.asyncio  # type: ignore[misc]
async def test_alarm_webhook_delivery_standard(
	test_client: QuartClient,
	login_mutation: str,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
	new_alarm_load_cell: dict[str, Any],
) -> None:
	"""When alarm has webhook_id, triggering sends Standard Webhooks format (signed payload + headers)."""
	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	# Create webhook first
	webhook_url = "http://webhook.example.com/receive"
	webhook_resp = await execute_graphql(
		CREATE_WEBHOOK_MUTATION,
		token=token,
		variables={"input": {"name": "Alarm Webhook", "url": webhook_url, "disabled": False}},
	)
	webhook_id = webhook_resp["data"]["createWebhook"]["id"]

	# Create alarm linked to webhook
	new_alarm_load_cell["webhookId"] = webhook_id
	resp = await execute_graphql(
		ALARM_MUTATION, token=token, variables={"input": new_alarm_load_cell}
	)
	assert resp["data"]["alarm"]["webhookId"] == webhook_id

	event = asyncio.Event()

	def receiver(sender: Any, **kwargs: Any) -> None:
		event.set()

	from src.signals import alarm_triggered

	alarm_triggered.connect(receiver)

	mock_response = Mock()
	mock_response.status_code = 200
	mock_response.text = ""
	mock_post = AsyncMock(return_value=mock_response)
	mock_instance = AsyncMock()
	mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
	mock_instance.__aexit__ = AsyncMock(return_value=False)
	mock_instance.post = mock_post

	# Standard Webhooks uses httpx in webhook_delivery module
	with patch("src.webhook_delivery.httpx.AsyncClient", return_value=mock_instance):
		await LoadCellPublisher().publish_n_messages(1)
		try:
			await asyncio.wait_for(event.wait(), timeout=2.0)
			await asyncio.sleep(0.1)
		finally:
			alarm_triggered.disconnect(receiver)

	mock_post.assert_called_once()
	url_called = mock_post.call_args.args[0]
	assert url_called == webhook_url

	# Standard Webhooks: content (JSON string), headers
	call_kwargs = mock_post.call_args.kwargs
	assert "content" in call_kwargs
	assert "headers" in call_kwargs

	headers = call_kwargs["headers"]
	assert "webhook-id" in headers
	assert "webhook-timestamp" in headers
	assert "webhook-signature" in headers
	assert headers["webhook-signature"].startswith("v1,")

	payload = json.loads(call_kwargs["content"])
	assert payload["type"] == "alarm.triggered"
	assert "timestamp" in payload
	assert "data" in payload
	data = payload["data"]
	assert data["alarm_name"] == new_alarm_load_cell["alarmName"]
	assert data["topic"] == new_alarm_load_cell["topic"]
	assert "message_data" in data


@pytest.mark.asyncio  # type: ignore[misc]
async def test_alarm_mqtt_forward(
	app: Any,
	test_client: QuartClient,
	login_mutation: str,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
	new_alarm_load_cell: dict[str, Any],
) -> None:
	"""When delivery_method='mqtt', a matching message is republished to forward_topic."""
	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	forward_topic = "sensors/loadcell/processed"
	new_alarm_load_cell["deliveryMethod"] = "mqtt"
	new_alarm_load_cell["forwardTopic"] = forward_topic

	resp = await execute_graphql(
		ALARM_MUTATION, token=token, variables={"input": new_alarm_load_cell}
	)
	assert "data" in resp and "alarm" in resp["data"], resp
	created = resp["data"]["alarm"]
	assert created["deliveryMethod"] == "mqtt"
	assert created["forwardTopic"] == forward_topic

	alarm_event = asyncio.Event()

	def alarm_receiver(sender: Any, **kwargs: Any) -> None:
		alarm_event.set()

	from src.signals import alarm_triggered

	alarm_triggered.connect(alarm_receiver)

	# Replace publish_callback directly on the alarm instance. The callback is a
	# stored bound-method reference set at Alarm.__init__ time; patching the class
	# after construction has no effect on it.
	mock_publish = AsyncMock()
	alarm_obj = app.app.mqtt_logger.alarm
	original_callback = alarm_obj.publish_callback
	alarm_obj.publish_callback = mock_publish

	try:
		await LoadCellPublisher().publish_n_messages(1)
		await asyncio.wait_for(alarm_event.wait(), timeout=2.0)
		# Yield control so trigger_alarm can finish awaiting publish_callback
		await asyncio.sleep(0.05)
	finally:
		alarm_triggered.disconnect(alarm_receiver)
		alarm_obj.publish_callback = original_callback

	mock_publish.assert_called_once()
	called_topic, called_payload = mock_publish.call_args.args
	assert called_topic == forward_topic
	forwarded = json.loads(called_payload)
	assert "measurement" in forwarded
	assert "weight" in forwarded["measurement"]


@pytest.mark.asyncio  # type: ignore[misc]
async def test_alarm_forward_topic_cannot_equal_topic(
	test_client: QuartClient,
	login_mutation: str,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
	new_alarm_load_cell: dict[str, Any],
) -> None:
	"""Creating an alarm where forward_topic == topic is rejected to prevent infinite loops."""
	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	new_alarm_load_cell["deliveryMethod"] = "mqtt"
	# Deliberately set forward_topic identical to topic
	new_alarm_load_cell["forwardTopic"] = new_alarm_load_cell["topic"]

	resp = await execute_graphql(
		ALARM_MUTATION, token=token, variables={"input": new_alarm_load_cell}
	)
	assert "errors" in resp, "Expected a GraphQL error but got none"
	assert "infinite loop" in resp["errors"][0]["message"]
