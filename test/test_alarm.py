# use graphql API to upload alarm script
# alarm and payload
# alarm includes topic/device
# try to parse payload as json or jsonpath
# then try to get path to desired location
# measurement.weight.value in example data
#


import pytest

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


@pytest.fixture
def new_alarm_input():
	return {
		"condition": "message['temperature'] > 75",
		"owner": "admin@agritheory.dev",
		"modifiedBy": "admin@agritheory.dev",
		"topic": "overheat",
		"alarmName": "Overheat Warning",
		"deliveryMethod": "email",
		"disabled": False,
	}


# --------------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_alarm(test_client, login_mutation, execute_graphql, new_alarm_input):
	# Authenticate
	login_resp = await execute_graphql(test_client, login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	# Create a new alarm
	resp = await execute_graphql(
		test_client,
		ALARM_MUTATION,
		token,
		{"input": new_alarm_input},
	)
	assert "data" in resp and "alarm" in resp["data"]
	alarm = resp["data"]["alarm"]

	# Basic sanity checks
	assert alarm["id"] is not None
	assert alarm["condition"] == new_alarm_input["condition"]
	assert alarm["topic"] == new_alarm_input["topic"]
	assert alarm["disabled"] is False


@pytest.mark.asyncio
async def test_get_alarms(test_client, login_mutation, execute_graphql):
	# Authenticate
	login_resp = await execute_graphql(test_client, login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	# Retrieve the list of alarms
	resp = await execute_graphql(test_client, GET_ALARMS_QUERY, token)
	assert "data" in resp and "getAlarms" in resp["data"]
	alarms = resp["data"]["getAlarms"]

	assert isinstance(alarms, list)
	assert len(alarms) > 0


@pytest.mark.asyncio
async def test_update_alarm(test_client, login_mutation, execute_graphql, new_alarm_input):
	# Authenticate
	login_resp = await execute_graphql(test_client, login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	# Create a fresh alarm to update
	create_resp = await execute_graphql(
		test_client,
		ALARM_MUTATION,
		token,
		{"input": new_alarm_input},
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
		test_client,
		ALARM_MUTATION,
		token,
		{"input": updated_input},
	)
	updated_alarm = update_resp["data"]["alarm"]

	assert updated_alarm["id"] == alarm_id
	assert updated_alarm["condition"] == updated_input["condition"]
	assert updated_alarm["disabled"] is True


@pytest.mark.asyncio
async def test_alarm_unauthorized(test_client, execute_graphql, new_alarm_input):
	# Attempt to create without a token
	resp = await execute_graphql(
		test_client,
		ALARM_MUTATION,
		None,
		{"input": new_alarm_input},
	)
	assert "errors" in resp
	assert "Authorization required" in resp["errors"][0]
