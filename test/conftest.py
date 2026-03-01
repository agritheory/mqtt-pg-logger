import os

# Configure testcontainers for different environments
if os.getenv("DEVCONTAINER"):
	os.environ["TESTCONTAINERS_HOST_OVERRIDE"] = "host.docker.internal"
else:
	# Running outside devcontainer - use localhost
	os.environ["TESTCONTAINERS_HOST_OVERRIDE"] = "localhost"
	# Ensure testcontainers can find docker
	os.environ["DOCKER_HOST"] = "unix:///var/run/docker.sock"

import json
import warnings
from collections.abc import AsyncGenerator, Generator
from typing import Any, cast

import pytest
import uvloop
from databases import Database
from quart.testing import QuartClient, TestApp
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs
from testcontainers.postgres import PostgresContainer
from websockets.asyncio.server import Server, ServerConnection, serve

from src.server import create_app


# Use uvloop for faster event loop
@pytest.fixture(scope="session")  # type: ignore[misc]
def event_loop_policy() -> uvloop.EventLoopPolicy:
	return uvloop.EventLoopPolicy()


# Suppress QuartAuth cookie warnings
warnings.filterwarnings(
	"ignore", message="The same attribute name/cookie name/salt is used by another QuartAuth instance"
)


@pytest.fixture(scope="session")  # type: ignore[misc]
def artemis_container() -> Generator[dict[str, Any], None, None]:
	"""Spin up an ActiveMQ Artemis container for the full test session."""
	container = DockerContainer("apache/activemq-artemis:latest-alpine")
	container.with_env("EXTRA_ARGS", "--http-host 0.0.0.0 --relax-jolokia")
	container.with_exposed_ports(1883)
	container.start()
	wait_for_logs(container, "AMQ221007", timeout=120)

	host = container.get_container_host_ip()
	port = int(container.get_exposed_port(1883))

	os.environ["MQTT_BROKER_HOST"] = host
	os.environ["MQTT_BROKER_PORT"] = str(port)
	# Default credentials for the apache/activemq-artemis image
	os.environ.setdefault("MQTT_USER", "artemis")
	os.environ.setdefault("MQTT_PASSWORD", "artemis")

	yield {"host": host, "port": port}

	container.stop()


@pytest.fixture  # type: ignore[misc]
async def app(db_url: str, artemis_container: dict[str, Any]) -> AsyncGenerator[TestApp, None]:
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

	# Configure container for different environments
	if not os.getenv("DEVCONTAINER"):
		# Outside devcontainer, bind to a specific port to avoid conflicts
		container.with_bind_ports(5432, None)  # Let testcontainers pick available port
	container.start()

	# _logger.info(f"Started TimescaleDB container: {container.get_connection_url()}")
	db = Database(container.get_connection_url())
	await db.connect()
	await db.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
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


async def echo(websocket: ServerConnection) -> None:
	async for message in websocket:
		await websocket.send(message)


@pytest.fixture  # type: ignore[misc]
async def websocket_server() -> AsyncGenerator[Server, None]:
	async with serve(echo, "127.0.0.1", 8765) as server:
		yield server
