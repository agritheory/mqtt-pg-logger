import os

os.environ["TESTCONTAINERS_HOST_OVERRIDE"] = "host.docker.internal"

import logging
from collections.abc import AsyncGenerator

import pytest
from databases import Database
from testcontainers.postgres import PostgresContainer

_logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")  # type: ignore[misc]
async def test_db() -> AsyncGenerator[Database, None]:
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
	_logger.info(f"Started TimescaleDB container: {container.get_connection_url()}")
	db = Database(container.get_connection_url())
	await db.connect()
	db.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
	await db.disconnect()
	yield db

	container.stop()


@pytest.mark.asyncio  # type: ignore[misc]
async def test_timescaledb_responds(test_db: Database) -> None:
	"""
	Connect via the `databases` library and run a simple SELECT 1.
	"""
	await test_db.connect()
	try:
		# fetch_val returns the first column of the first row
		value = await test_db.fetch_val("SELECT 1;")
	finally:
		await test_db.disconnect()

	assert value == 1
