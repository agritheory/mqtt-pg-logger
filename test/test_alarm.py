import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from test.load_cell_example_data import LoadCellPublisher
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from quart.testing import QuartClient

from src.alarm import CompiledAlarm

_logger = logging.getLogger(__name__)

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
    webhookUrl
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
	assert "Authorization required" in resp["errors"][0]


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

	def _receiver(sender: Any, **kwargs: Any) -> None:
		received.update(kwargs)
		event.set()

	from src.signals import alarm_triggered

	alarm_triggered.connect(_receiver)

	await LoadCellPublisher().publish_n_messages(1)

	try:
		await asyncio.wait_for(event.wait(), timeout=2.0)
	finally:
		alarm_triggered.disconnect(_receiver)

	_logger.info("Received: %s", received)
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

	def _receiver(sender: Any, **kwargs: Any) -> None:
		# record when signal arrives and payload
		received["timestamp"] = time.monotonic()
		received["kwargs"] = kwargs
		event.set()

	from src.signals import alarm_triggered

	alarm_triggered.connect(_receiver)

	# mark start time, publish, and wait for signal
	start_ts = time.monotonic()
	await LoadCellPublisher().publish_n_messages(1)

	try:
		await asyncio.wait_for(event.wait(), timeout=1.0)
	finally:
		alarm_triggered.disconnect(_receiver)

	# compute latency
	end_ts = received["timestamp"]
	latency = end_ts - start_ts
	# assert latency threshold
	_logger.info(f"Latency: {latency:.3f}s")
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

	def _receiver(sender: Any, **kwargs: Any) -> None:
		received.update(kwargs)
		event.set()

	from src.signals import alarm_triggered

	alarm_triggered.connect(_receiver)

	await LoadCellPublisher().publish_n_messages(1, payload_weight=500.0)

	try:
		await asyncio.wait_for(event.wait(), timeout=2.0)
	finally:
		alarm_triggered.disconnect(_receiver)

	_logger.info("Received: %s", received)
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
async def test_alarm_webhook_delivery(
	test_client: QuartClient,
	login_mutation: str,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
	new_alarm_load_cell: dict[str, Any],
) -> None:
	"""When webhook_url is set, triggering an alarm POSTs to that URL with the alarm payload."""
	webhook_url = "http://webhook.example.com/receive"

	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	new_alarm_load_cell["webhookUrl"] = webhook_url
	resp = await execute_graphql(
		ALARM_MUTATION, token=token, variables={"input": new_alarm_load_cell}
	)
	assert resp["data"]["alarm"]["webhookUrl"] == webhook_url

	event = asyncio.Event()

	def _receiver(sender: Any, **kwargs: Any) -> None:
		event.set()

	from src.signals import alarm_triggered

	alarm_triggered.connect(_receiver)

	mock_post = AsyncMock()
	mock_instance = AsyncMock()
	mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
	mock_instance.__aexit__ = AsyncMock(return_value=False)
	mock_instance.post = mock_post

	with patch("src.alarm.httpx.AsyncClient", return_value=mock_instance):
		await LoadCellPublisher().publish_n_messages(1)
		try:
			await asyncio.wait_for(event.wait(), timeout=2.0)
			# Signal fires before the async POST; yield briefly so the POST completes
			await asyncio.sleep(0.1)
		finally:
			alarm_triggered.disconnect(_receiver)

	mock_post.assert_called_once()
	url_called = mock_post.call_args.args[0]
	assert url_called == webhook_url
	json_payload = mock_post.call_args.kwargs["json"]
	assert json_payload["alarm_name"] == new_alarm_load_cell["alarmName"]
	assert json_payload["topic"] == new_alarm_load_cell["topic"]
	assert "message_data" in json_payload
