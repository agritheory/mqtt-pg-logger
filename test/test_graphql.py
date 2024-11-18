import asyncio
import json
import warnings
from typing import Any

import pytest
from quart.testing import QuartClient, TestApp

from src.create_schema import initialize_db
from src.server import create_app

# TODO: refactor to use conftest and app fixture with module scope
warnings.filterwarnings(
	"ignore", message="The same attribute name/cookie name/salt is used by another QuartAuth instance"
)


@pytest.fixture
async def app() -> TestApp:
	app = create_app()
	ctx = app.app_context()
	await ctx.push()

	await app.db.connect()

	try:
		await initialize_db()
	except Exception as e:
		print(f"Database initialization error: {e}")
		raise

	for startup in app.before_serving_funcs:
		await startup()

	await asyncio.sleep(2)

	yield app

	tasks = list(app.background_tasks)
	for task in tasks:
		if not task.done():
			task.cancel()

	pending_tasks = [t for t in tasks if not t.done()]
	if pending_tasks:
		try:
			await asyncio.gather(*pending_tasks, return_exceptions=True)
		except (asyncio.CancelledError, Exception) as e:
			print(f"Task cleanup error: {e}")

	await app.db.disconnect()
	try:
		await ctx.pop()
	except Exception as e:
		pass


@pytest.fixture
def test_client(app) -> QuartClient:
	return app.test_client()


@pytest.fixture
def login_mutation() -> str:
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
def topic_mutation() -> str:
	return """
		mutation {
			createTopic(input: {topic: "topic/device1"}) {
				id
			}
		}
	"""


@pytest.fixture
def topics_query() -> str:
	return """
		query {
			getTopics {
				topic
				creation
			}
		}
	"""


@pytest.fixture
def logout_mutation() -> str:
	return """
		mutation {
			logout
		}
	"""


@pytest.fixture
def refresh_token_mutation() -> str:
	return """
		mutation refreshToken($refresh_token: String!) {
			refreshToken(input: {refreshToken: $refresh_token}) {
				message
				accessToken
				refreshToken
				tokenType
				expiresIn
			}
		}
	"""


async def execute_graphql(
	client: QuartClient, query: str, token: str | None = None, variables: dict | None = None
) -> Any:
	headers = {"Content-Type": "application/json"}
	if token:
		headers["Authorization"] = f"Bearer {token}"

	request_body = {"query": query}
	if variables:
		request_body["variables"] = variables

	response = await client.post("/graphql/", json=request_body, headers=headers)
	return json.loads(await response.get_data())


@pytest.mark.asyncio
async def test_successful_login(test_client: QuartClient, login_mutation: str) -> None:
	response = await execute_graphql(test_client, login_mutation)
	assert "data" in response
	assert "login" in response["data"]
	assert response["data"]["login"]["message"] == "Login successful"
	assert "accessToken" in response["data"]["login"]
	assert "refreshToken" in response["data"]["login"]
	assert response["data"]["login"]["tokenType"] == "bearer"
	assert response["data"]["login"]["expiresIn"] > 0


@pytest.mark.asyncio
async def test_failed_login(test_client: QuartClient) -> None:
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
async def test_create_topic(
	test_client: QuartClient, login_mutation: str, topic_mutation: str
) -> None:
	login_response = await execute_graphql(test_client, login_mutation)
	token = login_response["data"]["login"]["accessToken"]
	response = await execute_graphql(test_client, topic_mutation, token)
	assert "data" in response
	assert len(response["data"]["createTopic"]) > 0


@pytest.mark.asyncio
async def test_topics_query_with_valid_token(
	test_client: QuartClient, login_mutation: str, topics_query: str
) -> None:
	login_response = await execute_graphql(test_client, login_mutation)
	token = login_response["data"]["login"]["accessToken"]
	response = await execute_graphql(test_client, topics_query, token)

	assert "data" in response
	assert "getTopics" in response["data"]
	assert len(response["data"]["getTopics"]) > 0


@pytest.mark.asyncio
async def test_topics_query_with_invalid_token(
	test_client: QuartClient, topics_query: str
) -> None:
	response = await execute_graphql(test_client, topics_query, "invalid_token")
	assert "errors" in response
	assert "Authorization required" in response["errors"][0]


@pytest.mark.asyncio
async def test_successful_logout(
	test_client: QuartClient, login_mutation: str, logout_mutation: str, topics_query: str
) -> None:
	login_response = await execute_graphql(test_client, login_mutation)
	token = login_response["data"]["login"]["accessToken"]

	logout_response = await execute_graphql(test_client, logout_mutation, token)
	assert "data" in logout_response
	assert logout_response["data"]["logout"] is True

	topics_response = await execute_graphql(test_client, topics_query, token)
	assert "errors" in topics_response
	assert "Authorization required" in topics_response["errors"][0]


@pytest.mark.asyncio
async def test_using_token_after_logout(
	test_client: QuartClient, login_mutation: str, logout_mutation: str, topics_query: str
) -> None:
	login_response = await execute_graphql(test_client, login_mutation)
	token = login_response["data"]["login"]["accessToken"]

	topics_response = await execute_graphql(test_client, topics_query, token)
	assert "data" in topics_response
	assert len(topics_response["data"]["getTopics"]) > 0

	await execute_graphql(test_client, logout_mutation, token)

	topics_response_after_logout = await execute_graphql(test_client, topics_query, token)
	assert "errors" in topics_response_after_logout
	assert "Authorization required" in topics_response_after_logout["errors"][0]


@pytest.mark.asyncio
async def test_successful_token_refresh(
	test_client: QuartClient, login_mutation: str, refresh_token_mutation: str
) -> None:
	login_response = await execute_graphql(test_client, login_mutation)
	token = login_response["data"]["login"]["accessToken"]
	refresh_token = login_response["data"]["login"]["refreshToken"]
	refresh_response = await execute_graphql(
		test_client, refresh_token_mutation, token, {"refresh_token": refresh_token}
	)
	assert "data" in refresh_response
	assert "refreshToken" in refresh_response["data"]
	assert refresh_response["data"]["refreshToken"]["message"] == "Token refresh successful"
	assert "accessToken" in refresh_response["data"]["refreshToken"]
	assert "refreshToken" in refresh_response["data"]["refreshToken"]
	assert refresh_response["data"]["refreshToken"]["tokenType"] == "bearer"
	assert refresh_response["data"]["refreshToken"]["expiresIn"] > 0


@pytest.mark.asyncio
async def test_failed_token_refresh(
	test_client: QuartClient, refresh_token_mutation: str, login_mutation: str
) -> None:
	login_response = await execute_graphql(test_client, login_mutation)
	token = login_response["data"]["login"]["accessToken"]
	refresh_token = login_response["data"]["login"]["refreshToken"]

	response = await execute_graphql(
		test_client,
		refresh_token_mutation,
		token,
		{"refresh_token": f"{refresh_token}invalidCharacters"},
	)

	assert "errors" in response
	assert "Invalid refresh token" in response["errors"][0]
	# assert "Invalid refresh token" in response["errors"][0]["message"]
