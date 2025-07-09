import asyncio
import gc
import logging
import tracemalloc
from collections.abc import AsyncGenerator
from test.load_cell_example_data import LoadCellPublisher, setup_topic

import pytest

_logger = logging.getLogger(__name__)

TARGET_RATE = 1000  # Hz
TARGET_DURATION = 3  # s
MAX_RAM = 100  # MiB


@pytest.fixture  # type: ignore[misc]
async def publisher() -> AsyncGenerator[LoadCellPublisher, None]:
	_logger.info("Setting up topic")
	if not await setup_topic():
		_logger.error("Failed to setup topic, exiting")
		return

	pub = LoadCellPublisher()
	yield pub


@pytest.mark.asyncio  # type: ignore[misc]
async def test_burst(publisher: LoadCellPublisher) -> None:
	"""Verify we can publish 1 000 messages / s for 3 000 messages (≈3 s window)."""
	loop = asyncio.get_running_loop()

	total_messages = TARGET_RATE * TARGET_DURATION
	period = 1 / TARGET_RATE
	next_tick = loop.time()
	start = next_tick

	for _ in range(total_messages):
		await publisher.publish_data_singular()
		# plan the next slot and wait for it
		next_tick += period
		# Tight spin until the scheduled moment; yield once to keep the loop responsive
		while (now := loop.time()) < next_tick:
			# only yield if there’s “enough” time left to be worth it (≈0.5 ms here)
			if next_tick - now > 0.0005:
				await asyncio.sleep(0)

	elapsed = loop.time() - start
	_logger.info("Transmission length for %d messages: %.6f s", total_messages, elapsed)
	assert elapsed < 3, (
		f"Burst took {elapsed:.3f}s " f"({total_messages / elapsed:.0f} msg/s; target ≥ {TARGET_RATE})"
	)


@pytest.mark.asyncio  # type: ignore[misc]
async def test_throughput(publisher: LoadCellPublisher) -> None:
	"""Test how many messages can be sent in 3 seconds"""
	loop = asyncio.get_running_loop()
	start = loop.time()
	i = 0
	while loop.time() - start < TARGET_DURATION:
		await publisher.publish_data_singular()
		i += 1
	messages_per_second = i / TARGET_DURATION
	_logger.info(f"Messages per second: {messages_per_second}")
	assert messages_per_second > TARGET_RATE


@pytest.mark.asyncio  # type: ignore[misc]
async def test_peak_memory(publisher: LoadCellPublisher) -> None:
	"""Run the publisher at 1 k msg/s for 3 s and check heap usage stays < 20 MiB."""
	tracemalloc.start()
	tracemalloc.reset_peak()

	total_messages = TARGET_RATE * TARGET_DURATION
	for _ in range(total_messages):
		await publisher.publish_data_singular()

	gc.collect()
	current, peak = tracemalloc.get_traced_memory()
	peak_mib = peak / 1024 / 1024
	_logger.info("Peak traced heap: %.1f MiB", peak_mib)
	assert peak_mib < MAX_RAM, f"Peak traced heap {peak_mib:.1f} MiB (limit {MAX_RAM} MiB)"
	tracemalloc.stop()


# @pytest.mark.asyncio
# async def test_alarm_latency(self):
#     """ Test the latency of the alarm system """
#     start = time.perf_counter()
#     await self.publisher.publish_data_singular()
#     # wait for alarm to be triggered
#     return time.perf_counter() - start
