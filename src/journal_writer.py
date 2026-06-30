"""Journal ingest: single-row or staging + promote batch modes."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import asyncpg
from environs import Env

if TYPE_CHECKING:
	from aiomqtt import PublishPacket

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JournalRow:
	topic: str
	text: str
	qos: int
	retain: bool
	entrypoint: str = "mqtt"
	priority: int = 0


@dataclass
class FlushResult:
	promoted: int = 0
	rejected: int = 0
	batch_id: uuid.UUID | None = None
	skipped_validation: int = 0


class JournalWriter:
	def __init__(self, db: asyncpg.Pool, env: Env | None = None) -> None:
		env = env or Env()
		env.read_env()
		self.db = db
		self.mode = env.str("JOURNAL_INGEST_MODE", "staging").lower()
		self.batch_size = env.int("JOURNAL_BATCH_SIZE", 100)
		self.batch_interval_ms = env.int("JOURNAL_BATCH_INTERVAL_MS", 50)
		max_payload = env.int("JOURNAL_MAX_PAYLOAD_BYTES", 0)
		self.max_payload_bytes = max_payload if max_payload > 0 else None
		self._buffer: list[JournalRow] = []
		self._flush_task: asyncio.Task[None] | None = None
		self.running = False
		self.validation_rejected = 0

	def prepare_row(self, message: PublishPacket) -> JournalRow | None:
		payload = message.payload
		if self.max_payload_bytes is not None and len(payload) > self.max_payload_bytes:
			self.validation_rejected += 1
			logger.warning("Rejecting message on topic %s: payload exceeds max size", message.topic)
			return None
		try:
			text = payload.decode("utf-8")
		except UnicodeDecodeError:
			self.validation_rejected += 1
			logger.warning("Rejecting message on topic %s: invalid UTF-8", message.topic)
			return None
		return JournalRow(
			topic=str(message.topic),
			text=text,
			qos=int(message.qos),
			retain=bool(message.retain),
		)

	async def store_row(self, row: JournalRow) -> None:
		await self.db.execute(
			"""
			INSERT INTO journal
			(topic, text, qos, retain, entrypoint, priority)
			VALUES ($1, $2, $3, $4, $5, $6)
			""",
			row.topic,
			row.text,
			row.qos,
			1 if row.retain else 0,
			row.entrypoint,
			row.priority,
		)

	async def store_one(self, message: PublishPacket) -> FlushResult:
		row = self.prepare_row(message)
		if row is None:
			return FlushResult(skipped_validation=1)
		await self.store_row(row)
		return FlushResult(promoted=1)

	async def append(self, message: PublishPacket) -> FlushResult:
		row = self.prepare_row(message)
		if row is None:
			return FlushResult(skipped_validation=1)
		if self.mode != "staging":
			await self.store_row(row)
			return FlushResult(promoted=1)
		self._buffer.append(row)
		if len(self._buffer) >= self.batch_size:
			return await self.flush()
		return FlushResult()

	async def flush(self) -> FlushResult:
		if not self._buffer:
			return FlushResult()

		batch_id = uuid.uuid4()
		rows = self._buffer
		self._buffer = []

		records = [
			(
				batch_id,
				row.topic,
				row.text,
				row.qos,
				row.retain,
				row.entrypoint,
				row.priority,
				"pending",
			)
			for row in rows
		]

		async with self.db.acquire() as conn:
			await conn.copy_records_to_table(
				"journal_staging",
				records=records,
				columns=[
					"batch_id",
					"topic",
					"text",
					"qos",
					"retain",
					"entrypoint",
					"priority",
					"status",
				],
			)
			row = await conn.fetchrow(
				"SELECT promoted, rejected FROM promote_journal_batch($1)",
				batch_id,
			)

		assert row is not None
		return FlushResult(
			promoted=int(row["promoted"]),
			rejected=int(row["rejected"]),
			batch_id=batch_id,
		)

	async def flush_if_due(self) -> FlushResult:
		if self.mode == "staging" and self._buffer:
			return await self.flush()
		return FlushResult()

	def start_flush_loop(self) -> None:
		if self.mode != "staging" or self._flush_task is not None:
			return
		self.running = True
		self._flush_task = asyncio.create_task(self.flush_loop())

	async def stop_flush_loop(self) -> FlushResult:
		self.running = False
		result = FlushResult()
		if self._flush_task is not None:
			self._flush_task.cancel()
			try:
				await self._flush_task
			except asyncio.CancelledError:
				pass
			self._flush_task = None
		result = await self.flush()
		return result

	async def flush_loop(self) -> None:
		interval = self.batch_interval_ms / 1000.0
		while self.running:
			await asyncio.sleep(interval)
			try:
				await self.flush_if_due()
			except Exception as e:
				logger.error("Journal flush loop error: %s", e)

	async def store_messages_batch(
		self,
		messages: list[PublishPacket],
		*,
		poison_every: int | None = None,
	) -> FlushResult:
		"""Benchmark helper: ingest many messages, optional periodic invalid payloads."""
		total = FlushResult()
		if self.mode == "staging":
			for index, message in enumerate(messages):
				if poison_every and index % poison_every == 0 and index > 0:
					self.validation_rejected += 1
					total.skipped_validation += 1
					continue
				row = self.prepare_row(message)
				if row is None:
					total.skipped_validation += 1
					continue
				self._buffer.append(row)
			flush_result = await self.flush()
			total.promoted += flush_result.promoted
			total.rejected += flush_result.rejected
			total.skipped_validation += flush_result.skipped_validation
			return total

		for index, message in enumerate(messages):
			if poison_every and index % poison_every == 0 and index > 0:
				total.skipped_validation += 1
				self.validation_rejected += 1
				continue
			result = await self.store_one(message)
			total.promoted += result.promoted
			total.rejected += result.rejected
			total.skipped_validation += result.skipped_validation
		return total
