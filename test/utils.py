import copy
import os
from test.setup_test import SetupTest

import yaml

from src.mqtt_listener import MqttConfKey


def create_config_file(test_config_data, database_config, topics):
	mqtt_config = copy.deepcopy(test_config_data["mqtt"])
	mqtt_config[MqttConfKey.SUBSCRIPTIONS] = topics

	data = {
		"database": database_config,
		"mqtt": mqtt_config,
	}

	config_file = SetupTest.get_test_path("config_file.yaml")
	with open(config_file, "w") as write_file:
		yaml.dump(data, write_file, default_flow_style=False)

	os.chmod(config_file, 0o600)

	return config_file
