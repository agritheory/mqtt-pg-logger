import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast

import aiomqtt
import asyncpg
import pytest
from environs import Env
from quart.testing import TestApp as QuartTestApp

from src.migrate import run_migrations

BENCHMARK_DIR = Path(__file__).parent


def pytest_addoption(parser: pytest.Parser) -> None:
	parser.addoption(
		"--benchmark-output",
		action="store",
		default=None,
		help="Write benchmark JSON report to this path",
	)
	parser.addoption(
		"--benchmark-baseline",
		action="store",
		default=None,
		help="Baseline JSON report for before/after comparison",
	)


@pytest.fixture  # type: ignore[misc]
def benchmark_output(request: pytest.FixtureRequest) -> str | None:
	return cast(str | None, request.config.getoption("--benchmark-output"))


@pytest.fixture  # type: ignore[misc]
def benchmark_baseline(request: pytest.FixtureRequest) -> str | None:
	return cast(str | None, request.config.getoption("--benchmark-baseline"))


@pytest.fixture  # type: ignore[misc]
async def benchmark_pool(db_url: str) -> AsyncGenerator[asyncpg.Pool, None]:
	pool = await asyncpg.create_pool(db_url)
	async with pool.acquire() as conn:
		await run_migrations(conn)
	yield pool
	await pool.close()


@pytest.fixture  # type: ignore[misc]
async def benchmark_app(
	db_url: str,
	artemis_container: dict[str, Any],
) -> AsyncGenerator[QuartTestApp, None]:
	from src.create_schema import create_admin_user
	from src.server import create_app

	os.environ["JOURNAL_INGEST_MODE"] = os.environ.get("JOURNAL_INGEST_MODE", "single")
	os.environ["ALLOW_ALL_TOPICS"] = "true"
	os.environ["LOG_ALL_TOPICS"] = "false"
	quart_app = create_app(db_url=db_url)
	async with quart_app.test_app() as test_app:
		pool: asyncpg.Pool = test_app.app.db
		await pool.execute(
			"""
			TRUNCATE alarm, webhook, journal, journal_staging, journal_rejected,
			topic, pid_state, revoked_token, "user"
			RESTART IDENTITY CASCADE
			"""
		)
		await create_admin_user(
			pool,
			os.environ["ADMIN_EMAIL"],
			os.environ["ADMIN_PASSWORD"],
			is_admin=True,
		)
		yield test_app


@pytest.fixture  # type: ignore[misc]
async def benchmark_mqtt_client() -> AsyncGenerator[aiomqtt.Client, None]:
	env = Env()
	username = env.str("MQTT_USER", "artemis")
	password = env.str("MQTT_PASSWORD", "artemis")
	host = os.environ.get("MQTT_BROKER_HOST", "localhost")
	port = int(os.environ.get("MQTT_BROKER_PORT", "1883"))

	async with aiomqtt.Client(
		hostname=host,
		port=port,
		username=username,
		password=password.encode(),
	) as client:
		yield client
