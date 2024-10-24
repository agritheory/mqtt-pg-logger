import asyncio
from test.mqtt_publisher import MqttPublisher
from test.setup_test import SetupTest
from test.utils import create_config_file

import attr
import pytest
import pytest_asyncio
from pytest_postgresql import factories

from src.app_config import AppConfig
from src.constants import MqttConfKey
from src.database import DatabaseConfKey
from src.mqtt_pg_logger import run_service


@attr.s
class Subscription:
    topic: str = attr.ib()
    subscription: str | None = attr.ib()
    skip: bool = attr.ib()


@attr.s
class SentMessage:
    subscription: Subscription = attr.ib()
    text: str = attr.ib()
    message_id: int = attr.ib(default=None)


postgresql_external = factories.postgresql_noproc(
    user="postgres",
    password="postgres",
)
postgresql = factories.postgresql("postgresql_external")


@pytest.fixture
def subscriptions():
    test_config_data = SetupTest.read_test_config()
    sub_base = test_config_data["mqtt"][MqttConfKey.TEST_SUBSCRIPTION_BASE]

    return [
        Subscription(
            topic=sub_base + "/inside/log1",
            subscription=sub_base + "/inside/#",
            skip=False,
        ),
        Subscription(
            topic=sub_base + "/inside/log2",
            subscription=sub_base + "/inside/#",
            skip=False,
        ),
        Subscription(topic=sub_base + "/outside", subscription=None, skip=True),
    ]


@pytest.fixture
def topics(subscriptions):
    return [s.subscription for s in subscriptions if s.subscription]


@pytest.fixture
def pg_config(postgresql):
    return {
        DatabaseConfKey.USER: postgresql.info.user,
        DatabaseConfKey.HOST: postgresql.info.host,
        DatabaseConfKey.PORT: postgresql.info.port,
        DatabaseConfKey.DATABASE: postgresql.info.dbname,
        DatabaseConfKey.PASSWORD: postgresql.info.password,
    }


@pytest.fixture
def config_file(pg_config, topics):
    SetupTest.ensure_test_dir()
    SetupTest.ensure_database_dir()
    test_config_data = SetupTest.read_test_config()
    test_config_data["mqtt"][MqttConfKey.CLIENT_ID] = "pg-test-consumer"
    config_file = create_config_file(test_config_data, pg_config, topics)
    return config_file


@pytest_asyncio.fixture
async def publisher():
    config_file = SetupTest.get_test_config_path()
    config = AppConfig(config_file)
    config._config_data["mqtt"][MqttConfKey.CLIENT_ID] = "pg-test-publisher"
    mqtt_publisher = MqttPublisher(config)
    return mqtt_publisher


@pytest.mark.asyncio
async def test_full_integration(
    config_file,
    postgresql,
    subscriptions,
    publisher: MqttPublisher,
):
    await run_service(config_file, True, None, "debug", True, True)  # create schema

    unique_id = 0
    message_queue = []
    for _ in range(1, 9):
        for subscription in subscriptions:
            unique_id = unique_id + 1
            message = SentMessage(
                subscription=subscription, text=f"{unique_id}-{subscription.topic}"
            )
            message_queue.append(message)

    async with asyncio.TaskGroup() as tg:
        loop_task = tg.create_task(run_service(config_file, False, None, "debug", True, True))

        sent_messages = []
        for message in message_queue:
            await publisher.publish(topic=message.subscription.topic, payload=message.text)
            if not message.subscription.skip:
                sent_messages.append(message)

        result = postgresql.execute("select text, topic from journal").fetchall()
        assert len(result) == len(sent_messages)

        stored_messages = {row[0]: row for row in result}
        for sent_message in sent_messages:
            stored_message = stored_messages.get(sent_message.text)
            assert stored_message
            assert stored_message[1] == sent_message.subscription.topic

        loop_task.cancel()
