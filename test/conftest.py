import asyncio
import json
import warnings

import pytest
import uvloop
from quart.testing import QuartClient, TestApp

from src.create_schema import initialize_db
from src.server import create_app


# Use uvloop for faster event loop
@pytest.fixture(scope="session")
def event_loop_policy() -> uvloop.EventLoopPolicy:
	return uvloop.EventLoopPolicy()


# Suppress QuartAuth cookie warnings
warnings.filterwarnings(
	"ignore", message="The same attribute name/cookie name/salt is used by another QuartAuth instance"
)


@pytest.fixture
async def app() -> TestApp:
	app = create_app()
	ctx = app.app_context()
	await ctx.push()

	await app.db.connect()
	await initialize_db()
	for fn in app.before_serving_funcs:
		await fn()
	# give background tasks a moment
	await asyncio.sleep(2)

	yield app

	# teardown background tasks
	for task in list(app.background_tasks):
		if not task.done():
			task.cancel()
	await app.db.disconnect()
	# pop the quart context if still active
	try:
		await ctx.pop()
	except LookupError:
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


@pytest.fixture
def execute_graphql(test_client: QuartClient):
	async def _exec(query: str, *, token: str | None = None, variables: dict | None = None):
		headers = {"Content-Type": "application/json"}
		if token:
			headers["Authorization"] = f"Bearer {token}"
		body = {"query": query}
		if variables:
			body["variables"] = variables
		resp = await test_client.post("/graphql/", json=body, headers=headers)
		return json.loads(await resp.get_data())

	return _exec
