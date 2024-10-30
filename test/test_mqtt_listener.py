from test.setup_test import SetupTest

import pytest

from src.app_config import AppConfig
from src.constants import MqttConfKey
from src.mqtt_listener import MqttListener


def create_listener(skip_subscriptions):
	config_file = SetupTest.get_test_config_path()
	config = AppConfig(config_file)
	config._config_data["mqtt"][MqttConfKey.SUBSCRIPTIONS] = ["base1/#", "base2/#"]
	config._config_data["mqtt"][MqttConfKey.SKIP_SUBSCRIPTION_REGEXES] = skip_subscriptions
	return MqttListener(config)


@pytest.mark.asyncio
async def test_accept_topic():
	listener = create_listener(["base1/exclude", "^base2/exclude"])
	assert listener.is_valid_topic("base1/exclude") is False
	assert listener.is_valid_topic("base1/exclude/2") is False
	assert listener.is_valid_topic("base1/exclude2") is False
	assert listener.is_valid_topic("base1/include") is True
	assert listener.is_valid_topic("base1/include/base2/exclude") is True
	assert listener.is_valid_topic("base1/include/exclude") is True
	assert listener.is_valid_topic("base2/exclude") is False
	assert listener.is_valid_topic("base2/exclude/2") is False
	assert listener.is_valid_topic("base2/exclude2") is False
