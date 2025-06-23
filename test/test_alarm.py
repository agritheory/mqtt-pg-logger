from collections.abc import Awaitable, Callable
from test.load_cell_example_data import amain as load_cell_data_async
from typing import Any

import pytest
from blinker import signal
from quart.testing import QuartClient

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
) -> None:
	# Authenticate
	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

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
	received = {}

	def receiver(sender: Any, **kwargs: Any) -> None:
		received["alarm"] = kwargs.get("alarm")
		received["data"] = kwargs.get("message_data")

	triggered = signal("alarm_triggered")
	triggered.connect(receiver)

	await load_cell_data_async(continuous=False)

	alarm = received.get("alarm")
	assert alarm is not None, "No alarm was received"
	assert alarm.alarm_name == new_alarm_load_cell["alarmName"]

	triggered.disconnect(receiver)
