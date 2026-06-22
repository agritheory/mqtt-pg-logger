"""End-to-end and in-process journal ingest benchmarks."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from test.benchmark.report import (
	RunMetrics,
	build_report,
	compare_to_baseline,
	write_report,
)
from typing import Any

import aiomqtt
import asyncpg
import pytest
from environs import Env
from mqtt5 import PublishPacket, QoS
from quart.testing import TestApp as QuartTestApp

from src.journal_writer import JournalWriter

logger = logging.getLogger(__name__)

BENCHMARK_TOPIC = "benchmark/ingest"


def benchmark_config() -> dict[str, int | str]:
	env = Env()
	env.read_env()
	messages = env.int("BENCHMARK_MESSAGES", 10_000)
	e2e_messages = env.int("BENCHMARK_E2E_MESSAGES", messages)
	warmup = env.int("BENCHMARK_WARMUP", 1_000)
	e2e_warmup = env.int("BENCHMARK_E2E_WARMUP", min(warmup, 100))
	min_ingest_rate = env.float("BENCHMARK_MIN_INGEST_RATE", 10.0)
	return {
		"messages": messages,
		"e2e_messages": e2e_messages,
		"runs": env.int("BENCHMARK_RUNS", 20),
		"warmup": warmup,
		"e2e_warmup": e2e_warmup,
		"min_ingest_rate": min_ingest_rate,
		"ingest_mode": env.str("JOURNAL_INGEST_MODE", "single").lower(),
		"poison_every": env.int("BENCHMARK_POISON_EVERY", 100),
	}


def make_publish_packet(topic: str, payload: bytes, *, qos: int = 0) -> PublishPacket:
	kwargs: dict[str, Any] = {
		"topic": topic,
		"payload": payload,
		"qos": QoS(qos),
		"retain": False,
	}
	if qos > 0:
		kwargs["packet_id"] = 1
	return PublishPacket(**kwargs)


def make_message_batch(count: int, *, topic: str = BENCHMARK_TOPIC) -> list[PublishPacket]:
	return [
		make_publish_packet(
			topic,
			json.dumps({"sequence": index, "value": index * 0.1}).encode(),
		)
		for index in range(count)
	]


async def publish_benchmark_message(mqtt_client: aiomqtt.Client, payload: bytes) -> None:
	await mqtt_client.publish(
		BENCHMARK_TOPIC,
		payload=payload,
		qos=QoS.AT_LEAST_ONCE,
		packet_id=next(mqtt_client.packet_ids),
	)


async def publish_benchmark_messages(
	mqtt_client: aiomqtt.Client,
	payloads: list[bytes],
) -> None:
	for index, payload in enumerate(payloads):
		await publish_benchmark_message(mqtt_client, payload)
		if index > 0 and index % 500 == 0:
			await asyncio.sleep(0)


async def count_journal(pool: asyncpg.Pool) -> int:
	row = await pool.fetchrow("SELECT COUNT(*) AS count FROM journal")
	return int(row["count"])


async def truncate_journal_tables(pool: asyncpg.Pool) -> None:
	await pool.execute(
		"""
		TRUNCATE journal, journal_staging, journal_rejected RESTART IDENTITY CASCADE
		"""
	)


async def run_layer_a(pool: asyncpg.Pool, config: dict[str, int | str]) -> RunMetrics:
	messages = int(config["messages"])
	warmup = int(config["warmup"])
	poison_every = int(config["poison_every"])
	writer = JournalWriter(pool)

	await truncate_journal_tables(pool)

	warmup_messages = make_message_batch(warmup)
	await writer.store_messages_batch(warmup_messages)

	await truncate_journal_tables(pool)
	start_count = await count_journal(pool)

	batch = make_message_batch(messages)
	start = time.perf_counter()
	result = await writer.store_messages_batch(batch, poison_every=poison_every)
	elapsed = time.perf_counter() - start

	end_count = await count_journal(pool)
	promoted = end_count - start_count
	expected_valid = messages - (messages // poison_every if poison_every else 0)
	assert promoted >= expected_valid - 1, (
		f"Expected ~{expected_valid} journal rows, got {promoted} "
		f"(skipped={result.skipped_validation}, rejected={result.rejected})"
	)

	rate = promoted / elapsed if elapsed > 0 else 0.0
	return RunMetrics(
		ingest_rate_msg_s=rate,
		promoted_count=promoted,
		rejected_count=result.rejected + result.skipped_validation,
		elapsed_s=elapsed,
	)


async def wait_for_journal_count(
	pool: asyncpg.Pool,
	expected: int,
	*,
	timeout_s: float | None = None,
	min_ingest_rate: float = 10.0,
	journal_writer: JournalWriter | None = None,
) -> None:
	if timeout_s is None:
		timeout_s = max(120.0, expected / min_ingest_rate + 60.0)
	deadline = time.monotonic() + timeout_s
	while time.monotonic() < deadline:
		if journal_writer is not None:
			await journal_writer.flush_if_due()
		if await count_journal(pool) >= expected:
			return
		await asyncio.sleep(0.05)
	raise TimeoutError(
		f"Timed out waiting for {expected} journal rows (have {await count_journal(pool)})"
	)


async def wait_for_mqtt_ingest(
	app: QuartTestApp,
	pool: asyncpg.Pool,
	mqtt_client: aiomqtt.Client,
	*,
	timeout_s: float = 30.0,
) -> None:
	mqtt_logger = app.app.mqtt_logger
	deadline = time.monotonic() + timeout_s
	while time.monotonic() < deadline:
		if mqtt_logger.is_connected():
			break
		await asyncio.sleep(0.05)
	else:
		raise TimeoutError("Timed out waiting for MQTT logger to connect")

	await truncate_journal_tables(pool)
	probe_payload = json.dumps({"probe": True}).encode()
	await publish_benchmark_message(mqtt_client, probe_payload)
	await wait_for_journal_count(pool, 1, timeout_s=timeout_s)
	await truncate_journal_tables(pool)


async def run_layer_b(
	app: QuartTestApp,
	pool: asyncpg.Pool,
	mqtt_client: aiomqtt.Client,
	config: dict[str, int | str],
) -> RunMetrics:
	messages = int(config["e2e_messages"])
	warmup = min(int(config["e2e_warmup"]), messages)
	min_ingest_rate = float(config["min_ingest_rate"])
	pool = app.app.db
	journal_writer: JournalWriter = app.app.mqtt_logger.journal_writer

	await truncate_journal_tables(pool)
	warmup_payload = json.dumps({"warmup": True}).encode()
	await publish_benchmark_messages(
		mqtt_client,
		[warmup_payload] * warmup,
	)
	await wait_for_journal_count(
		pool,
		warmup,
		min_ingest_rate=min_ingest_rate,
		journal_writer=journal_writer,
	)
	await truncate_journal_tables(pool)

	start_count = await count_journal(pool)
	start = time.perf_counter()
	payloads = [json.dumps({"sequence": index}).encode() for index in range(messages)]
	await publish_benchmark_messages(mqtt_client, payloads)

	await wait_for_journal_count(
		pool,
		start_count + messages,
		min_ingest_rate=min_ingest_rate,
		journal_writer=journal_writer,
	)
	elapsed = time.perf_counter() - start
	promoted = await count_journal(pool) - start_count
	rate = promoted / elapsed if elapsed > 0 else 0.0
	return RunMetrics(
		ingest_rate_msg_s=rate,
		promoted_count=promoted,
		elapsed_s=elapsed,
	)


@pytest.mark.benchmark  # type: ignore[misc]
@pytest.mark.asyncio  # type: ignore[misc]
async def test_benchmark_journal_ingest(
	benchmark_pool: asyncpg.Pool,
	benchmark_app: QuartTestApp,
	benchmark_mqtt_client: aiomqtt.Client,
	benchmark_output: str | None,
	benchmark_baseline: str | None,
) -> None:
	config = benchmark_config()
	os.environ["JOURNAL_INGEST_MODE"] = str(config["ingest_mode"])

	layer_a_runs: list[RunMetrics] = []
	for run_index in range(int(config["runs"])):
		logger.info("Layer A run %s/%s", run_index + 1, config["runs"])
		layer_a_runs.append(await run_layer_a(benchmark_pool, config))

	await wait_for_mqtt_ingest(benchmark_app, benchmark_app.app.db, benchmark_mqtt_client)

	layer_b_runs: list[RunMetrics] = []
	for run_index in range(int(config["runs"])):
		logger.info("Layer B run %s/%s", run_index + 1, config["runs"])
		layer_b_runs.append(
			await run_layer_b(benchmark_app, benchmark_app.app.db, benchmark_mqtt_client, config)
		)

	report = build_report(
		ingest_mode=str(config["ingest_mode"]),
		messages_per_run=int(config["messages"]),
		runs=int(config["runs"]),
		warmup_messages=int(config["warmup"]),
		layer_metrics={"layer_a": layer_a_runs, "layer_b": layer_b_runs},
	)
	if report.layers["layer_b"].summary is not None:
		report.layers["layer_b"].summary["e2e_messages_per_run"] = int(config["e2e_messages"])

	if benchmark_baseline:
		from pathlib import Path

		compare_to_baseline(report, Path(benchmark_baseline))

	if benchmark_output:
		from pathlib import Path

		write_report(Path(benchmark_output), report)

	for layer_name, layer in report.layers.items():
		stats = layer.summary["ingest_rate_msg_s"] if layer.summary else {}
		logger.info(
			"%s ingest_rate_msg_s mean=%.1f stdev=%.1f ci95=[%.1f, %.1f]",
			layer_name,
			stats.get("mean", 0),
			stats.get("stdev", 0),
			stats.get("ci95_low", 0),
			stats.get("ci95_high", 0),
		)

	if report.comparison:
		for layer_name, comp in report.comparison.items():
			logger.info(
				"%s vs baseline: speedup=%.2fx p=%.4f significant=%s",
				layer_name,
				comp["speedup"],
				comp["p_value"],
				comp["significant_at_0_05"],
			)

	assert report.layers["layer_a"].summary is not None
	assert report.layers["layer_a"].summary["ingest_rate_msg_s"]["mean"] > 0
	assert report.layers["layer_b"].summary is not None
	assert report.layers["layer_b"].summary["ingest_rate_msg_s"]["mean"] > 0
