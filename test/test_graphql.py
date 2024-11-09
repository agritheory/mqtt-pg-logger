#!/usr/bin/env python3

import json

import pytest

from src.server import start_app


@pytest.fixture
def app():
	app = start_app()
	return app


@pytest.fixture
def test_client(app):
	return app.test_client()


@pytest.fixture
def login_mutation():
	return """
		mutation {
			login(input: {username: "admin@agritheory.dev", password: "ohch4GeiSie"}) {
				message
				accessToken
				refreshToken
				tokenType
				expiresIn
			}
		}
	"""


@pytest.fixture
def me_query():
	return """
		query {
			me {
				username
			}
		}
	"""


@pytest.fixture
def logout_mutation():
	return """
		mutation {
			logout
		}
	"""


async def execute_graphql(client, query, token=None):
	headers = {"Content-Type": "application/json"}
	if token:
		headers["Authorization"] = f"Bearer {token}"

	response = await client.post("/graphql/", json={"query": query}, headers=headers)

	return json.loads(await response.get_data())


@pytest.mark.asyncio
async def test_successful_login(test_client, login_mutation):
	response = await execute_graphql(test_client, login_mutation)
	print(response)
	assert "data" in response
	assert "login" in response["data"]
	assert response["data"]["login"]["message"] == "Login successful"
	assert "accessToken" in response["data"]["login"]
	assert "refreshToken" in response["data"]["login"]
	assert response["data"]["login"]["tokenType"] == "bearer"
	assert response["data"]["login"]["expiresIn"] > 0


@pytest.mark.asyncio
async def test_failed_login(test_client):
	bad_login_mutation = """
		mutation {
			login(input: { username: "admin", password: "wrongpassword" }) {
				message
				accessToken
			}
		}
	"""

	response = await execute_graphql(test_client, bad_login_mutation)
	assert "errors" in response
	assert "Invalid credentials" in response["errors"][0]


@pytest.mark.asyncio
async def test_me_query_with_valid_token(test_client, login_mutation, me_query):
	# First login to get token
	login_response = await execute_graphql(test_client, login_mutation)
	token = login_response["data"]["login"]["accessToken"]

	# Then query me endpoint
	response = await execute_graphql(test_client, me_query, token)

	assert "data" in response
	assert "me" in response["data"]
	assert response["data"]["me"]["username"] == "admin"


@pytest.mark.asyncio
async def test_me_query_without_token(test_client, me_query):
	response = await execute_graphql(test_client, me_query)

	assert "errors" in response
	assert "Not authenticated" in response["errors"][0]


@pytest.mark.asyncio
async def test_me_query_with_invalid_token(test_client, me_query):
	response = await execute_graphql(test_client, me_query, "invalid_token")

	assert "errors" in response
	assert "Not authenticated" in response["errors"][0]


@pytest.mark.asyncio
async def test_successful_logout(test_client, login_mutation, logout_mutation, me_query):
	# First login to get token
	login_response = await execute_graphql(test_client, login_mutation)
	token = login_response["data"]["login"]["accessToken"]

	# Then logout
	logout_response = await execute_graphql(test_client, logout_mutation, token)
	assert "data" in logout_response
	assert logout_response["data"]["logout"] is True

	# Verify token is no longer valid by trying to use it
	me_response = await execute_graphql(test_client, me_query, token)
	assert "errors" in me_response
	assert "Not authenticated" in me_response["errors"][0]


@pytest.mark.asyncio
async def test_logout_without_token(test_client, logout_mutation):
	response = await execute_graphql(test_client, logout_mutation)

	assert "errors" in response
	assert "Not authenticated" in response["errors"][0]


@pytest.mark.asyncio
async def test_logout_with_invalid_token(test_client, logout_mutation):
	response = await execute_graphql(test_client, logout_mutation, "invalid_token")

	assert "errors" in response
	assert "Not authenticated" in response["errors"][0]


@pytest.mark.asyncio
async def test_using_token_after_logout(test_client, login_mutation, logout_mutation, me_query):
	# Login to get token
	login_response = await execute_graphql(test_client, login_mutation)
	token = login_response["data"]["login"]["accessToken"]

	# First verify token works
	me_response = await execute_graphql(test_client, me_query, token)
	assert "data" in me_response
	assert me_response["data"]["me"]["username"] == "admin"

	# Logout
	await execute_graphql(test_client, logout_mutation, token)

	# Try to use token again
	me_response_after_logout = await execute_graphql(test_client, me_query, token)
	assert "errors" in me_response_after_logout
	assert "Not authenticated" in me_response_after_logout["errors"][0]
