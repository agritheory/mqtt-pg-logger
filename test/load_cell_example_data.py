import asyncio
import json
import logging
import random
import socket
import sys
from datetime import datetime, timedelta

import httpx
from aiomqtt import Client, MqttError
from environs import Env

# Configure logging and environment
logging.basicConfig(
	level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)
env = Env()


async def setup_topic():
	"""Setup authentication and create topic if it doesn't exist"""
	login_mutation = """
		mutation Login($input: LoginInput!) {
			login(input: $input) {
				access_token
				message
			}
		}
	"""

	create_topic_mutation = """
		mutation CreateTopic($input: TopicInput!) {
			createTopic(input: $input) {
				id
				topic
			}
		}
	"""

	async with httpx.AsyncClient(verify=False) as client:
		# Login to get access token
		try:
			login_response = await client.post(
				"http://localhost:5000/graphql",
				json={
					"query": login_mutation,
					"variables": {"input": {"username": "admin@agritheory.dev", "password": "ohch4GeiSie"}},
				},
			)
			login_data = login_response.json()
			logger.debug(f"Login response: {login_data}")

			if "errors" in login_data:
				logger.error(f"Login failed: {login_data['errors']}")
				return False

			access_token = login_data["data"]["login"]["access_token"]

			# Create topic using the access token
			headers = {"Authorization": f"Bearer {access_token}"}
			topic_name = "sensors/loadcell/#"

			create_response = await client.post(
				"http://localhost:5000/graphql",
				headers=headers,
				json={
					"query": create_topic_mutation,
					"variables": {"input": {"topic": topic_name, "disabled": False}},
				},
			)

			create_data = create_response.json()
			logger.debug(f"Create topic response: {create_data}")

			if "errors" in create_data:
				if "already exists" not in str(create_data["errors"]):
					logger.error(f"Topic creation failed: {create_data['errors']}")
					return False
				else:
					logger.info("Topic already exists")
			else:
				logger.info(f"Topic created successfully: {topic_name}")

			return True

		except Exception as e:
			logger.error(f"Setup failed: {e}", exc_info=True)
			return False


def resolve_host(hostname: str) -> str:
	"""Convert Docker service names to localhost if not running in Docker"""
	try:
		socket.gethostbyname(hostname)
		return hostname
	except socket.gaierror:
		logger.debug(f"Could not resolve {hostname}, falling back to localhost")
		return "127.0.0.1"


class LoadCellPublisher:
	def __init__(
		self,
		broker: str = None,
		port: int = None,
		capacity_lb: int = None,
		username: str = None,
		password: str = None,
		keepalive: int = None,
		qos: int = None,
	):
		self.broker = resolve_host(broker or env.str("MQTT_BROKER_HOST", "artemis"))
		self.port = port or env.int("MQTT_BROKER_PORT", 1883)
		self.username = username or env.str("MQTT_USER", "artemis")
		self.password = password or env.str("MQTT_PASSWORD", "artemis")
		self.keepalive = keepalive or env.int("MQTT_KEEPALIVE", 60)
		self.qos = qos or env.int("MQTT_DEFAULT_QUALITY", 1)
		self.capacity_lb = capacity_lb or 500

		self.device_id = f"RL20000SS-{self.capacity_lb}LB"
		self.sequence = 0
		self.last_calibration = datetime(2024, 10, 1, 8, 0, 0)

		# RL20000SS specific configurations
		self.capacity_lb = capacity_lb
		self.rated_output = 3.0  # mV/V
		self.output_tolerance = 0.008  # mV/V
		self.excitation_voltage = 10.0  # VDC (within 5-10V range)
		self.combined_error = 0.0003  # 0.03% expressed as decimal

		logger.info(f"Initialized publisher for device {self.device_id}")

	async def publish_data(self, interval=1.0):
		"""Publish load cell data at specified interval."""
		logger.info(f"Attempting to connect to MQTT broker at {self.broker}:{self.port}")
		try:
			async with Client(
				hostname=self.broker,
				port=self.port,
				username=self.username,
				password=self.password,
				keepalive=self.keepalive,
			) as client:
				logger.info(f"Connected to MQTT broker at {self.broker}:{self.port}")

				while True:
					try:
						payload = self.generate_payload()
						topic = f"sensors/loadcell/{self.device_id}/data"

						logger.debug(f"Publishing to topic: {topic}")
						logger.debug(f"Payload: {json.dumps(payload, indent=2)}")

						await client.publish(topic=topic, payload=json.dumps(payload), qos=self.qos)

						logger.info(f"Published reading: {payload['measurement']['weight']['value']} lb")
						await asyncio.sleep(interval)

					except Exception as e:
						logger.error(f"Error during publish: {e}", exc_info=True)
						await asyncio.sleep(5)  # Wait before retry

		except MqttError as e:
			logger.error(f"MQTT Connection Error: {e}", exc_info=True)
			if "Not authorized" in str(e):
				logger.error("Authentication failed. Please verify:")
				logger.error("1. MQTT_USER and MQTT_PASSWORD environment variables are set correctly")
				logger.error("2. The credentials have proper permissions on the broker")
			elif "Connection refused" in str(e):
				logger.error("Connection refused. Please verify that:")
				logger.error("1. The MQTT broker is running")
				logger.error("2. The broker address and port are correct")
				logger.error("3. If using docker-compose, ensure the service is up")
		except Exception as e:
			logger.error(f"Unexpected error: {e}", exc_info=True)

	def generate_payload(self):
		"""Generate a realistic Rice Lake RL20000SS load cell reading with metadata."""
		self.sequence += 1

		# Simulate a realistic weight (60-80% of capacity for typical industrial use)
		target_load = self.capacity_lb * random.uniform(0.6, 0.8)
		# Add realistic noise and error
		load_error = target_load * self.combined_error * random.uniform(-1, 1)
		actual_load = target_load + load_error

		# Calculate raw signal based on RL20000SS specifications
		raw_signal = (actual_load / self.capacity_lb) * self.rated_output
		raw_signal += random.uniform(-self.output_tolerance, self.output_tolerance)

		# Generate temperature within operating range
		temperature_c = random.uniform(20, 35)
		temp_status = self.check_temperature_limits(temperature_c)

		# Calculate realistic output and input resistance with specifications
		output_resistance = 350 + random.uniform(-3.5, 3.5)  # 350 ± 3.5 ohm
		input_resistance = 390 + random.uniform(-15, 15)  # 390 ± 15 ohm

		# Warning flags based on actual specifications
		warning_flags = []
		if temp_status == "operating":
			warning_flags.append("temperature_outside_compensated_range")
		if temp_status == "out_of_range":
			warning_flags.append("temperature_outside_operating_range")
		if actual_load > self.capacity_lb:
			warning_flags.append("approaching_rated_capacity")

		return {
			"device": {
				"id": self.device_id,
				"type": "load_cell",
				"manufacturer": "Rice Lake",
				"model": "RL20000SS",
				"capacity_lb": self.capacity_lb,
				"location": "Production-Line-1",
			},
			"timestamp": datetime.utcnow().isoformat() + "Z",
			"sequence": self.sequence,
			"measurement": {
				"weight": {
					"value": round(actual_load, 2),
					"unit": "lb",
					"precision": 0.01,
					"status": "stable" if abs(load_error) < (self.capacity_lb * 0.0001) else "settling",
				},
				"raw_signal": {"value": round(raw_signal, 4), "unit": "mV/V"},
			},
			"specifications": {
				"output_resistance": round(output_resistance, 2),
				"input_resistance": round(input_resistance, 2),
				"excitation_voltage": round(self.excitation_voltage, 2),
				"combined_error_percent": 0.03,
			},
			"calibration": {
				"last_calibration": self.last_calibration.isoformat() + "Z",
				"next_calibration_due": (self.last_calibration + timedelta(days=180)).isoformat() + "Z",
				"ntep_certification": "CC 98-078",
				"environment_rating": "IP67",
			},
			"diagnostics": {
				"temperature": {"value": round(temperature_c, 1), "unit": "celsius", "status": temp_status},
				"signal_quality": round(99 - abs(load_error / actual_load) * 100, 1),
				"error_flags": [],
				"warning_flags": warning_flags,
			},
			"metadata": {
				"batch_id": f"B{datetime.now().strftime('%y-%m-%d-%p')}",
				"product_code": "PROD-392",
				"cable_details": {
					"length_ft": 20,
					"wiring": {
						"red": "+Excitation",
						"black": "-Excitation",
						"green": "+Signal",
						"white": "-Signal",
					},
				},
			},
		}

	def check_temperature_limits(self, temp_c):
		"""Check if temperature is within specified ranges."""
		if -10 <= temp_c <= 40:
			return "compensated"
		elif -18 <= temp_c <= 65:
			return "operating"
		else:
			return "out_of_range"


async def amain():
	logger.info("Starting publisher")

	# Setup topic before starting publisher
	logger.info("Setting up topic...")
	if not await setup_topic():
		logger.error("Failed to setup topic, exiting")
		return

	publisher = LoadCellPublisher()
	try:
		await publisher.publish_data(interval=2.0)
	except KeyboardInterrupt:
		logger.info("Shutting down publisher")
	except Exception as e:
		logger.error(f"Error in main: {e}", exc_info=True)
		raise


def main():
	"""Entry point for the poetry script."""
	try:
		logger.info("Starting Load Cell Publisher")
		asyncio.run(amain())
	except KeyboardInterrupt:
		logger.info("Publisher stopped by user")
	except Exception as e:
		logger.error(f"Fatal error: {e}", exc_info=True)
		sys.exit(1)


if __name__ == "__main__":
	main()
