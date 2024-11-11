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
def topics_query():
	return """
		query {
			getTopics {
				topic
				creation
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


@pytest.fixture
def refresh_token_mutation():
	return """
		mutation {
			refreshToken(input: {refreshToken: "valid_refresh_token"}) {
				message
				accessToken
				refreshToken
				tokenType
				expiresIn
			}
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
async def test_topics_query_with_valid_token(test_client, login_mutation, topics_query):
	login_response = await execute_graphql(test_client, login_mutation)
	token = login_response["data"]["login"]["accessToken"]
	response = await execute_graphql(test_client, topics_query, token)

	assert "data" in response
	assert "getTopics" in response["data"]
	assert len(response["data"]["getTopics"]) > 0


@pytest.mark.asyncio
async def test_topics_query_without_token(test_client, topics_query):
	response = await execute_graphql(test_client, topics_query)

	assert "errors" in response
	assert "Not authenticated" in response["errors"][0]


@pytest.mark.asyncio
async def test_topics_query_with_invalid_token(test_client, topics_query):
	response = await execute_graphql(test_client, topics_query, "invalid_token")

	assert "errors" in response
	assert "Not authenticated" in response["errors"][0]


@pytest.mark.asyncio
async def test_successful_logout(test_client, login_mutation, logout_mutation, topics_query):
	login_response = await execute_graphql(test_client, login_mutation)
	token = login_response["data"]["login"]["accessToken"]

	logout_response = await execute_graphql(test_client, logout_mutation, token)
	assert "data" in logout_response
	assert logout_response["data"]["logout"] is True

	topics_response = await execute_graphql(test_client, topics_query, token)
	assert "errors" in topics_response
	assert "Not authenticated" in topics_response["errors"][0]


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
async def test_using_token_after_logout(
	test_client, login_mutation, logout_mutation, topics_query
):
	login_response = await execute_graphql(test_client, login_mutation)
	token = login_response["data"]["login"]["accessToken"]

	topics_response = await execute_graphql(test_client, topics_query, token)
	assert "data" in topics_response
	assert len(topics_response["data"]["getTopics"]) > 0

	await execute_graphql(test_client, logout_mutation, token)

	topics_response_after_logout = await execute_graphql(test_client, topics_query, token)
	assert "errors" in topics_response_after_logout
	assert "Not authenticated" in topics_response_after_logout["errors"][0]


@pytest.mark.asyncio
async def test_successful_token_refresh(test_client, login_mutation, refresh_token_mutation):
	login_response = await execute_graphql(test_client, login_mutation)
	refresh_token = login_response["data"]["login"]["refreshToken"]

	refresh_response = await execute_graphql(test_client, refresh_token_mutation, refresh_token)
	assert "data" in refresh_response
	assert "refreshToken" in refresh_response["data"]
	assert refresh_response["data"]["refreshToken"]["message"] == "Token refresh successful"
	assert "accessToken" in refresh_response["data"]["refreshToken"]
	assert "refreshToken" in refresh_response["data"]["refreshToken"]
	assert refresh_response["data"]["refreshToken"]["tokenType"] == "bearer"
	assert refresh_response["data"]["refreshToken"]["expiresIn"] > 0


@pytest.mark.asyncio
async def test_failed_token_refresh(test_client, refresh_token_mutation):
	response = await execute_graphql(test_client, refresh_token_mutation, "invalid_refresh_token")
	assert "errors" in response
	assert "Invalid refresh token" in response["errors"][0]
