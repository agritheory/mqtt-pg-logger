import asyncio
import datetime
import json
import warnings
from collections.abc import AsyncGenerator
from typing import Any

import aiomqtt
import pytest
from environs import Env

from src.server import create_app

env = Env()

warnings.filterwarnings(
	"ignore", message="The same attribute name/cookie name/salt is used by another QuartAuth instance"
)


@pytest.fixture  # type: ignore[misc]
async def app(**kwargs: str) -> AsyncGenerator[None, None]:
	app = create_app(force_rollback=True)
	ctx = app.app_context()
	await ctx.push()

	await app.db.connect()
	async with app.test_app() as test_app:
		yield test_app


@pytest.fixture  # type: ignore[misc]
async def mqtt_client() -> AsyncGenerator[aiomqtt.Client, None]:
	username = env.str("MQTT_USER", "artemis")
	password = env.str("MQTT_PASSWORD", "artemis")

	try:
		async with aiomqtt.Client(
			hostname="localhost",
			port=1883,
			username=username,
			password=password,
		) as client:
			yield client
	except aiomqtt.MqttError as e:
		pytest.skip(f"MQTT Broker not available: {str(e)}")


@pytest.mark.asyncio  # type: ignore[misc]
async def test_mqtt_message_logging(
	app: Any,
	mqtt_client: aiomqtt.Client,
) -> None:
	# Print background tasks status
	print("\nBackground tasks at start:")
	for task in app.background_tasks:
		print(f"Task {task}: {task.done()}")

	test_topic = "test/logging"
	test_payload = {
		"message": "test message",
		"timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
	}

	# Log before publishing
	print(f"\nPublishing message to {test_topic}")
	await mqtt_client.publish(test_topic, payload=json.dumps(test_payload).encode(), qos=1)

	# Wait for message to be processed
	await asyncio.sleep(2)

	# Query for the record
	record = await app.db.fetch_one(
		query="""
			SELECT * FROM journal
			WHERE topic = :topic
			ORDER BY creation DESC
			LIMIT 1
		""",
		values={
			"topic": test_topic,
		},
	)

	# Print all records for debugging
	print("\nChecking database records:")
	all_records = await app.db.fetch_all(query="SELECT * FROM journal ORDER BY creation DESC LIMIT 5")
	print("\nLast 5 records in database:")
	for r in all_records:
		print(f"Topic: {r['topic']}, Payload: {r['payload']}")

	# Print background tasks status again
	print("\nBackground tasks at end:")
	for task in app.background_tasks:
		print(f"Task {task}: {task.done()}")

	assert record is not None, "No matching record found in database"
	assert record["topic"] == test_topic
	assert record["payload"] == json.dumps(test_payload)
