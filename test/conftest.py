import json
import warnings
from asyncio import AbstractEventLoop
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast

import pytest
import uvloop
import yaml  # type: ignore[import-untyped]
from databases import Database
from environs import Env
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
async def app() -> TestApp:
	app = create_app(force_rollback=True)
	ctx = app.app_context()
	await ctx.push()

	await app.db.connect()
	async with app.test_app() as test_app:
		yield test_app


@pytest.fixture  # type: ignore[misc]
def test_client(app: TestApp) -> QuartClient:
	return app.test_client()


@pytest.fixture(scope="session")  # type: ignore[misc]
async def test_db_url(event_loop: AbstractEventLoop) -> AsyncGenerator[str, None]:
	"""Starts a TimescaleDB container, enables the extension, yields the async URL."""

	def get_timescale_image() -> str:
		"""Retrieve the TimescaleDB image from the Docker Compose file."""
		compose_file_path = Path(__file__).parent.parent / "docker-compose.yml"
		with compose_file_path.open("r") as file:
			compose_data = yaml.safe_load(file)
		return cast(str, compose_data["services"]["timescaledb"]["image"])

	TS_IMAGE = get_timescale_image()
	with PostgresContainer(
		image=TS_IMAGE, user="postgres", password="postgres", dbname="test_db"
	) as pg:

		# wait for the container to be healthy
		engine_url = pg.get_connection_url()
		# convert for databases/asyncpg
		async_url = engine_url.replace("postgresql://", "postgresql+asyncpg://")

		# connect and enable the extension
		db = Database(async_url)
		await db.connect()
		await db.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
		yield async_url
		await db.disconnect()


@pytest.fixture  # type: ignore[misc]
async def test_db(test_db_url: str) -> AsyncGenerator[Database, None]:
	"""Gives you a connected Database instance with Timescale enabled."""
	db = Database(test_db_url)
	await db.connect()
	try:
		env = Env()
		env.read_env()
		from src.create_schema import initialize_db

		# fernet config
		fernet_key = env.str("FERNET_KEY", None)
		admin_email = env.str("ADMIN_EMAIL", None)
		admin_password = env.str("ADMIN_PASSWORD", None)
		mqtt_user = env.str("MQTT_USER")
		await initialize_db(
			app.db,
			fernet_key=fernet_key,
			admin_email=admin_email,
			admin_password=admin_password,
			mqtt_user=mqtt_user,
		)
	except Exception as e:
		raise

	yield db
	await db.disconnect()


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
