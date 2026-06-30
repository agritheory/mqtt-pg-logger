import asyncio
import logging
import ssl
import time
from typing import Any

import asyncpg
from aiomqtt import Client as AIOMQTTClient
from aiomqtt import ConnectError, NegativeAckError, ProtocolError, PublishPacket
from environs import Env
from mqtt5 import PubRelPacket, QoS, TopicFilter

from src.alarm import Alarm
from src.journal_writer import JournalWriter
from src.signals import topic_signal

logger = logging.getLogger(__name__)

MAX_RECONNECT_DELAY = 60.0


class MQTTLogger:
	def __init__(self, db: asyncpg.Pool, broker_url: str = "localhost", broker_port: int = 1883):
		env = Env()
		env.read_env()
		self.running = False
		self.db = db
		self.broker_url = broker_url or env.str("MQTT_BROKER_HOST")
		self.broker_port = broker_port or env.int("MQTT_BROKER_PORT")
		self.username = env.str("MQTT_USER")
		self.password = env.str("MQTT_PASSWORD")
		self.identifier = env.str("MQTT_CLIENT_ID") or "mqtt-logger"
		self.keepalive = env.int("MQTT_KEEPALIVE") or 1
		self.ssl_context = self.build_ssl_context(env)
		self.log_all_topics = env.bool("LOG_ALL_TOPICS", False)
		self.allow_all_topics = env.bool("ALLOW_ALL_TOPICS", False)
		self.topics = {"#"} if self.allow_all_topics else set()
		self.topic_signal = topic_signal
		self.topic_signal.connect(self.add_topic)
		self._active_client: AIOMQTTClient | None = None
		self.journal_writer = JournalWriter(db, env)

		self.alarm = Alarm(publish_callback=self.forward)

	@property
	def effective_topics(self) -> set:
		return self.topics

	def is_connected(self) -> bool:
		return self._active_client is not None

	def build_ssl_context(self, env: Env) -> ssl.SSLContext | None:
		if env.bool("SSL_INSECURE"):
			return None

		# TODO not tested or implemented
		context = ssl.create_default_context(cafile=env.str("SSL_CA_CERTS"))
		context.load_cert_chain(
			certfile=env.str("SSL_CERTFILE"),
			keyfile=env.str("SSL_KEYFILE"),
		)
		return context

	def client(self) -> AIOMQTTClient:
		password = self.password.encode() if self.password else None
		return AIOMQTTClient(
			hostname=self.broker_url,
			port=self.broker_port,
			username=self.username,
			password=password,
			identifier=self.identifier,
			keep_alive=self.keepalive,
			ssl_context=self.ssl_context,
		)

	async def get_topics(self) -> set:
		rows = await self.db.fetch(
			"""
			SELECT id, topic, disabled, creation, modified, owner, modified_by
			FROM topic
			WHERE disabled = false
			ORDER BY topic
			"""
		)
		if not rows:
			return {"#"}  # fallback if no topics are configured
		return {row["topic"] for row in rows}

	async def wait_before_reconnect(self, delay: float) -> None:
		deadline = time.monotonic() + delay
		while self.running and time.monotonic() < deadline:
			await asyncio.sleep(min(0.1, deadline - time.monotonic()))

	async def start(self) -> None:
		"""Start MQTT client and subscribe to topics with reconnect."""
		self.running = True
		self.journal_writer.running = True
		self.journal_writer.start_flush_loop()
		reconnect_delay = 1.0

		while self.running:
			if self.allow_all_topics:
				self.topics = {"#"}
			else:
				self.topics = await self.get_topics()
			try:
				async with self.client() as client:
					self._active_client = client
					reconnect_delay = 1.0
					logger.info(f"MQTT client connected to {self.broker_url}:{self.broker_port}")
					logger.info(f"Filtering on topics {self.effective_topics}")
					for topic in tuple(self.effective_topics):
						await client.subscribe(TopicFilter(topic, max_qos=QoS.AT_LEAST_ONCE))

					async for message in client.messages():
						if not self.running:
							break
						if isinstance(message, PubRelPacket):
							continue
						await self.handle_message(message)

			except (ConnectError, NegativeAckError, ProtocolError, OSError) as e:
				logger.error(f"MQTT client error: {e}")
			finally:
				self._active_client = None

			if not self.running:
				break

			logger.info("Reconnecting to MQTT broker in %.1fs", reconnect_delay)
			await self.wait_before_reconnect(reconnect_delay)
			reconnect_delay = min(reconnect_delay * 2, MAX_RECONNECT_DELAY)

	async def forward(self, topic: str, payload: str) -> None:
		"""Publish a message back to the broker (used by filter/forward alarms)."""
		if self._active_client is not None:
			await self._active_client.publish(
				topic,
				payload.encode(),
				qos=QoS.AT_LEAST_ONCE,
				packet_id=next(self._active_client.packet_ids),
			)
		else:
			logger.warning(f"Cannot forward to '{topic}': MQTT client not connected")

	async def handle_message(self, message: PublishPacket) -> None:
		try:
			await self.store_message(message)
		except Exception as e:
			logger.error(f"Failed to store message: {e}")

		try:
			await self.alarm.handle_message(message)
		except Exception as e:
			logger.error(f"Failed to handle alarm: {e}")

	async def store_message(self, message: PublishPacket) -> None:
		topic_str = str(message.topic)
		topics_snapshot = tuple(self.effective_topics)

		if self.log_all_topics and topic_str not in topics_snapshot:
			logger.info(f"Topic added: '{message.topic}'")
			await self.save_topic(message)

		if not self.allow_all_topics and topic_str not in topics_snapshot:
			logger.warning(f"Message not collected: '{message.topic}'")
			return

		await self.journal_writer.append(message)

	async def save_topic(self, message: PublishPacket) -> None:
		await self.db.execute(
			"""
			INSERT INTO topic (topic, disabled, owner, modified_by)
			VALUES ($1, $2, $3, $4)
			ON CONFLICT (topic) DO NOTHING
			""",
			str(message.topic),
			False,
			self.username,
			self.username,
		)

	async def add_topic(self, sender: Any, **kwargs: str) -> None:
		topic = kwargs.get("topic")
		if topic:
			self.topics.add(topic)

	async def stop(self) -> None:
		"""Stop the MQTT logger."""
		self.running = False
		self.topic_signal.disconnect(self.add_topic)
		await self.journal_writer.stop_flush_loop()
		logger.info("MQTT logger stopped")
