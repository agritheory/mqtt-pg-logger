import logging
from typing import Any

import aiomqtt
import asyncpg
from aiomqtt import Client as AIOMQTTClient
from aiomqtt import ProtocolVersion, TLSParameters
from environs import Env

from src.alarm import Alarm
from src.signals import topic_signal

logger = logging.getLogger(__name__)


class MQTTLogger:
	def __init__(self, db: asyncpg.Pool, broker_url: str = "localhost", broker_port: int = 1883):
		env = Env()
		env.read_env()
		self._running = False
		self.db = db
		self.broker_url = broker_url or env.str("MQTT_BROKER_HOST")
		self.broker_port = broker_port or env.int("MQTT_BROKER_PORT")
		self.username = env.str("MQTT_USER")
		self.password = env.str("MQTT_PASSWORD")
		self.identifier = env.str("MQTT_CLIENT_ID") or "mqtt-logger"
		self.keepalive = env.int("MQTT_KEEPALIVE") or 1
		self.protocol = env.int("MQTT_DEFAULT_PROTOCOL") or 5
		self.tls_params = {}
		self.log_all_topics = env.bool("LOG_ALL_TOPICS", False)
		self.allow_all_topics = env.bool("ALLOW_ALL_TOPICS", False)
		self.topics = {"#"} if self.allow_all_topics else set()
		self.topic_signal = topic_signal
		self.topic_signal.connect(self.add_topic)
		self._active_client: AIOMQTTClient | None = None

		if not env.bool("SSL_INSECURE"):
			# TODO not tested or implemented
			self.tls_params = TLSParameters(
				ca_certs=env.str("SSL_CA_CERTS"),
				certfile=env.str("SSL_CERTFILE"),
				keyfile=env.str("SSL_KEYFILE"),
			)

		self.alarm = Alarm(publish_callback=self.forward)

	@property
	def effective_topics(self) -> set:
		return self.topics

	def client(self) -> AIOMQTTClient:
		_client = AIOMQTTClient(
			hostname=self.broker_url,
			port=self.broker_port,
			username=self.username,
			password=self.password,
			identifier=self.identifier,
			protocol=ProtocolVersion(self.protocol),
			keepalive=self.keepalive,
		)
		if self.tls_params:
			_client.tls_params = self.tls_params
		return _client

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

	async def start(self) -> None:
		"""Start MQTT client and subscribe to topics"""
		self.topics = await self.get_topics()
		try:
			async with self.client() as client:
				self._active_client = client
				logger.info(f"MQTT client connected to {self.broker_url}:{self.broker_port}")
				logger.info(f"Filtering on topics {self.effective_topics}")
				# Snapshot to avoid "Set changed size during iteration" when add_topic runs concurrently
				for topic in tuple(self.effective_topics):
					await client.subscribe(topic=topic, qos=1)

				async for message in client.messages:
					await self.handle_message(message)

		except Exception as e:
			logger.error(f"Failed to start MQTT client: {e}")
			raise
		finally:
			self._active_client = None

	async def forward(self, topic: str, payload: str) -> None:
		"""Publish a message back to the broker (used by filter/forward alarms)."""
		if self._active_client is not None:
			await self._active_client.publish(topic, payload)
		else:
			logger.warning(f"Cannot forward to '{topic}': MQTT client not connected")

	async def handle_message(self, message: aiomqtt.Message) -> None:
		try:
			await self.store_message(message)
		except Exception as e:
			logger.error(f"Failed to store message: {e}")

		try:
			await self.alarm.handle_message(message)
		except Exception as e:
			logger.error(f"Failed to handle alarm: {e}")

	async def store_message(self, message: aiomqtt.Message) -> None:
		# Snapshot to avoid "Set changed size during iteration" when add_topic runs concurrently
		topics_snapshot = tuple(self.effective_topics)
		if self.log_all_topics:
			if str(message.topic) not in topics_snapshot:
				logger.info(f"Topic added: '{message.topic}'")
				await self.save_topic(message)
		else:
			logger.warning(f"Topic not collected: '{message.topic}'")
		if not self.allow_all_topics:
			if str(message.topic) not in topics_snapshot:
				logger.warning(f"Message not collected: '{message.topic}'")
				return

		await self.db.execute(
			"""
			INSERT INTO journal
			(topic, text, qos, retain, entrypoint, priority)
			VALUES ($1, $2, $3, $4, $5, $6)
			""",
			str(message.topic),
			message.payload.decode(),
			message.qos,
			message.retain,
			"mqtt",
			0,
		)

	async def save_topic(self, message: aiomqtt.Message) -> None:
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
		"""Stop the MQTT logger"""
		self._running = False
		try:
			if hasattr(self.client, "disconnect"):
				await self.client.disconnect()
		except Exception as e:
			logger.error(f"Error disconnecting MQTT client: {e}")
		logger.info("MQTT logger stopped")
