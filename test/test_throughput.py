import asyncio
import gc
import logging
import os
import tracemalloc
from collections.abc import AsyncGenerator, Awaitable, Callable
from test.load_cell_example_data import LoadCellPublisher
from typing import Any

import pytest
from quart.testing import QuartClient

_logger = logging.getLogger(__name__)

# Separate targets for sequential vs concurrent publish patterns.
# Sequential rate is limited by per-message broker round-trip; concurrent batches many
# in-flight publishes and is significantly faster. Both default to conservative values
# that pass through Docker/WSL2 networking.
# Override via env for bare-metal benchmarking, e.g. MQTT_TARGET_RATE_SEQUENTIAL=1000.
TARGET_RATE_SEQUENTIAL = int(os.getenv("MQTT_TARGET_RATE_SEQUENTIAL", "100"))
TARGET_RATE_CONCURRENT = int(os.getenv("MQTT_TARGET_RATE_CONCURRENT", "500"))
TARGET_DURATION = 3  # s
MAX_RAM = 100  # MiB


@pytest.fixture  # type: ignore[misc]
async def publisher(
	test_client: QuartClient,
	login_mutation: str,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
) -> AsyncGenerator[LoadCellPublisher, None]:
	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]
	await execute_graphql(
		'mutation { createTopic(input: {topic: "sensors/loadcell/#"}) { id } }',
		token=token,
	)
	yield LoadCellPublisher()


@pytest.mark.asyncio  # type: ignore[misc]
async def test_throughput(publisher: LoadCellPublisher) -> None:
	"""Test how many awaited messages can be sent in a short time"""
	i = 0
	async with await publisher._mqtt_client() as client:
		loop = asyncio.get_running_loop()
		start = loop.time()
		while (loop.time() - start) < TARGET_DURATION:
			await publisher.publish_data_static(client)
			i += 1
		elapsed = loop.time() - start

	messages_per_second = i / elapsed
	_logger.info(f"Messages per second: {messages_per_second}")
	assert messages_per_second > TARGET_RATE_SEQUENTIAL


@pytest.mark.asyncio  # type: ignore[misc]
async def test_concurrent_throughput(publisher: LoadCellPublisher) -> None:
	"""Test how many concurrent messages can be sent in a short time"""
	total = TARGET_RATE_CONCURRENT * TARGET_DURATION
	loop = asyncio.get_running_loop()
	start = loop.time()
	await publisher.publish_n_messages(total)
	elapsed = loop.time() - start

	rate = total / elapsed
	_logger.info(f"Concurrent burst rate: {rate:.1f} msg/s over {elapsed:.3f}s")
	assert rate > TARGET_RATE_CONCURRENT


@pytest.mark.asyncio  # type: ignore[misc]
async def test_peak_memory(publisher: LoadCellPublisher) -> None:
	"""Run the publisher and check heap usage stays < 20 MiB."""
	tracemalloc.start()
	tracemalloc.reset_peak()

	async with await publisher._mqtt_client() as client:
		loop = asyncio.get_running_loop()
		start = loop.time()
		while (loop.time() - start) < TARGET_DURATION:
			await publisher.publish_data_static(client)

	gc.collect()
	current, peak = tracemalloc.get_traced_memory()
	peak_mib = peak / 1024 / 1024
	_logger.info("Peak traced heap: %.1f MiB", peak_mib)
	assert peak_mib < MAX_RAM, f"Peak traced heap {peak_mib:.1f} MiB (limit {MAX_RAM} MiB)"
	tracemalloc.stop()
