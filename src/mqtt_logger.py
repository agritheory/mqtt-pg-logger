import logging

from aiomqtt import Client as AIOMQTTClient
from aiomqtt import ProtocolVersion, TLSParameters
from databases import Database
from environs import Env

_logger = logging.getLogger(__name__)


class MQTTLogger:
	"""Handles MQTT subscription and message logging"""

	def __init__(self, db: Database, broker_url: str = "localhost", broker_port: int = 1883):
		env = Env()
		env.read_env()
		self._running = False
		self.db = db
		self.table_name = env.str("TABLE_NAME")
		self.broker_url = broker_url or env.str("MQTT_BROKER_HOST")
		self.broker_port = broker_port or env.int("MQTT_BROKER_PORT")
		self.username = env.str("MQTT_USER")
		self.password = env.str("MQTT_PASSWORD")
		self.identifier = env.str("MQTT_CLIENT_ID") or "mqtt-logger"
		self.keepalive = env.int("MQTT_KEEPALIVE") or 1
		self.protocol = env.int("MQTT_DEFAULT_PROTOCOL") or 5
		self.topics = self.get_topics()
		self.tls_params = {}

		if not env.bool("SSL_INSECURE"):
			# TODO not tested or implemented
			self.tls_params = TLSParameters(
				ca_certs=env.str("SSL_CA_CERTS"),
				certfile=env.str("SSL_CERTFILE"),
				keyfile=env.str("SSL_KEYFILE"),
			)

	def client(self):
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

	def get_topics(self):
		return "#"
		# TODO look up topics in database

	async def start(self):
		"""Start MQTT client and subscribe to topics"""
		try:
			async with self.client() as client:
				_logger.info(f"MQTT client connected to {self.broker_url}:{self.broker_port}")
				for topic in self.topics:
					await client.subscribe(topic=topic, qos=1)

				async for message in client.messages:
					_logger.info(f"Payload: {message.payload}")
					await self.store_message(message)

		except Exception as e:
			_logger.error(f"Failed to start MQTT client: {e}")
			raise

	async def store_message(self, message) -> None:
		query = """
			INSERT INTO pgqueuer
			(topic, text, qos, retain, entrypoint, priority, status)
			VALUES (
				:topic,
				:text,
				:qos,
				:retain,
				:entrypoint,
				:priority,
				:status
			)
			RETURNING id
		"""

		values = {
			"topic": str(message.topic),
			"text": message.payload.decode(),
			"qos": message.qos,
			"retain": message.retain,
			"entrypoint": "mqtt",
			"priority": 0,
			"status": "queued",
		}
		async with self.db.transaction():
			await self.db.execute(query=query, values=values)

	async def stop(self):
		"""Stop the MQTT logger"""
		self._running = False
		if hasattr(self, "client") and self.client:
			try:
				if hasattr(self.client, "disconnect"):
					await self.client.disconnect()
			except Exception as e:
				_logger.error(f"Error disconnecting MQTT client: {e}")
		_logger.info("MQTT logger stopped")
