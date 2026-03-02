import asyncio
import datetime
import json
import os
import warnings
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

import aiomqtt
import pytest
from environs import Env
from quart.testing import QuartClient
from quart.testing import TestApp as QuartTestApp

env = Env()

warnings.filterwarnings(
	"ignore", message="The same attribute name/cookie name/salt is used by another QuartAuth instance"
)


@pytest.fixture  # type: ignore[misc]
async def mqtt_client() -> AsyncGenerator[aiomqtt.Client, None]:
	username = env.str("MQTT_USER", "artemis")
	password = env.str("MQTT_PASSWORD", "artemis")
	host = os.environ.get("MQTT_BROKER_HOST", "localhost")
	port = int(os.environ.get("MQTT_BROKER_PORT", "1883"))

	async with aiomqtt.Client(
		hostname=host,
		port=port,
		username=username,
		password=password,
	) as client:
		yield client


@pytest.mark.asyncio  # type: ignore[misc]
async def test_mqtt_message_logging(
	app: QuartTestApp,
	test_client: QuartClient,
	mqtt_client: aiomqtt.Client,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
	login_mutation: str,
) -> None:
	test_topic = "test/logging"
	test_payload = {
		"message": "test message",
		"timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
	}

	# Register the topic so the MQTTLogger's Python-side filter accepts messages for it.
	# createTopic emits topic_signal, which calls MQTTLogger.add_topic in the background task.
	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]
	await execute_graphql(
		f'mutation {{ createTopic(input: {{topic: "{test_topic}"}}) {{ id }} }}',
		token=token,
	)
	# Brief yield so the topic_signal propagates to the background task before we publish.
	await asyncio.sleep(0.1)

	await mqtt_client.publish(test_topic, payload=json.dumps(test_payload).encode(), qos=1)

	# Wait for the background task to process and store the message.
	await asyncio.sleep(2)

	record = await app.app.db.fetchrow(
		"""
		SELECT * FROM journal
		WHERE topic = $1
		ORDER BY creation DESC
		LIMIT 1
		""",
		test_topic,
	)

	assert record is not None, "No matching record found in database"
	assert record["topic"] == test_topic
	assert record["payload"] == json.dumps(test_payload)
