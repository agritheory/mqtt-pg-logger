import os

# Configure testcontainers for different environments
if os.getenv("DEVCONTAINER"):
	os.environ["TESTCONTAINERS_HOST_OVERRIDE"] = "host.docker.internal"
else:
	os.environ["TESTCONTAINERS_HOST_OVERRIDE"] = "localhost"
	os.environ["DOCKER_HOST"] = "unix:///var/run/docker.sock"

import json
import warnings
from collections.abc import AsyncGenerator, Generator
from typing import Any, cast

import asyncpg
import pytest
import uvloop
from cryptography.fernet import Fernet
from quart.testing import QuartClient
from quart.testing import TestApp as QuartTestApp
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy
from testcontainers.postgres import PostgresContainer
from websockets.asyncio.server import Server, ServerConnection, serve

from src.create_schema import create_admin_user
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
	container.waiting_for(LogMessageWaitStrategy("AMQ221007").with_startup_timeout(120))
	container.start()

	host = container.get_container_host_ip()
	port = int(container.get_exposed_port(1883))

	os.environ["MQTT_BROKER_HOST"] = host
	os.environ["MQTT_BROKER_PORT"] = str(port)
	os.environ.setdefault("MQTT_USER", "artemis")
	os.environ.setdefault("MQTT_PASSWORD", "artemis")

	yield {"host": host, "port": port}

	container.stop()


@pytest.fixture  # type: ignore[misc]
async def app(
	db_url: str, artemis_container: dict[str, Any]
) -> AsyncGenerator[QuartTestApp, None]:
	_app = create_app(db_url=db_url)
	async with _app.test_app() as test_app:
		# before_serving has now run: schema exists, pool is live.
		# Truncate all data tables so every test starts with a clean slate.
		# CASCADE handles FK ordering (alarm → webhook → user).
		pool: asyncpg.Pool = test_app.app.db
		await pool.execute('TRUNCATE alarm, webhook, journal, topic, "user" RESTART IDENTITY CASCADE')
		# Re-seed the admin user that all tests authenticate as.
		fernet = Fernet(os.environ["FERNET_KEY"])
		await create_admin_user(
			pool,
			fernet,
			os.environ["ADMIN_EMAIL"],
			os.environ["ADMIN_PASSWORD"],
		)
		yield test_app


@pytest.fixture  # type: ignore[misc]
def test_client(app: QuartTestApp) -> QuartClient:
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

	if not os.getenv("DEVCONTAINER"):
		container.with_bind_ports(5432, None)
	container.start()

	conn_url = container.get_connection_url()
	# asyncpg uses postgresql://, psycopg2 (used by testcontainers) uses postgresql+psycopg2://
	asyncpg_url = conn_url.replace("postgresql+psycopg2://", "postgresql://")

	pool = await asyncpg.create_pool(asyncpg_url)
	await pool.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
	await pool.close()

	yield asyncpg_url

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

	async def run_graphql(
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

	return run_graphql


async def echo(websocket: ServerConnection) -> None:
	async for message in websocket:
		await websocket.send(message)


@pytest.fixture  # type: ignore[misc]
async def websocket_server() -> AsyncGenerator[Server, None]:
	async with serve(echo, "127.0.0.1", 8765) as server:
		yield server
