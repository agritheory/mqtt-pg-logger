import os

os.environ["TESTCONTAINERS_HOST_OVERRIDE"] = "host.docker.internal"

import json
import warnings
from collections.abc import AsyncGenerator
from typing import Any, cast

import pytest
import uvloop
from databases import Database
from quart.testing import QuartClient, TestApp
from testcontainers.postgres import PostgresContainer

from src.server import create_app


# Use uvloop for faster event loop
@pytest.fixture(scope="session")  # type: ignore[misc]
def event_loop_policy() -> uvloop.EventLoopPolicy:
	return uvloop.EventLoopPolicy()


# Suppress QuartAuth cookie warnings
warnings.filterwarnings(
	"ignore", message="The same attribute name/cookie name/salt is used by another QuartAuth instance"
)


@pytest.fixture  # type: ignore[misc]
async def app(db_url: str) -> AsyncGenerator[TestApp, None]:
	app = create_app(db_url=db_url, force_rollback="True")
	ctx = app.app_context()
	await ctx.push()

	# await app.db.connect()
	async with app.test_app() as test_app:
		yield test_app


@pytest.fixture  # type: ignore[misc]
def test_client(app: TestApp) -> QuartClient:
	return app.test_client()


@pytest.fixture(scope="session")  # type: ignore[misc]
async def db_url() -> AsyncGenerator[str, None]:
	"""
	Start a TimescaleDB container (built on Postgres), create the timescaledb extension,
	and yield the connection URL.
	"""

	image = "timescale/timescaledb:latest-pg16"

	container = PostgresContainer(
		image,
		dbname="test_db",
		username="postgres",
		password="postgres",
	)
	container.start()

	# _logger.info(f"Started TimescaleDB container: {container.get_connection_url()}")
	db = Database(container.get_connection_url())
	await db.connect()
	db.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
	await db.disconnect()
	yield container.get_connection_url()

	container.stop()


@pytest.fixture  # type: ignore[misc]
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


@pytest.fixture  # type: ignore[misc]
def topic_mutation() -> str:
	return """
        mutation {
            createTopic(input: {topic: "topic/device1"}) {
                id
            }
        }
    """


@pytest.fixture  # type: ignore[misc]
def topics_query() -> str:
	return """
        query {
            getTopics {
                topic
                creation
            }
        }
    """


@pytest.fixture  # type: ignore[misc]
def logout_mutation() -> str:
	return """
        mutation {
            logout
        }
    """


@pytest.fixture  # type: ignore[misc]
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


@pytest.fixture  # type: ignore[misc]
def execute_graphql(test_client: QuartClient) -> Any:
	"""
	Pytest fixture returning an async helper for executing GraphQL queries
	against the Quart test client and parsing the JSON result.
	"""

	async def _exec(
		query: str,
		*,
		token: str | None = None,
		variables: dict[str, Any] | None = None,
	) -> dict[str, Any]:
		headers: dict[str, str] = {"Content-Type": "application/json"}
		if token:
			headers["Authorization"] = f"Bearer {token}"

		body: dict[str, Any] = {"query": query}
		if variables is not None:
			body["variables"] = variables

		resp = await test_client.post("/graphql/", json=body, headers=headers)
		return cast(dict[str, Any], json.loads(await resp.get_data()))

	return _exec
