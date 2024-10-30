import os

import yaml
from jsonschema import validate

from src.constants import CONFIG_JSONSCHEMA


class AppConfig:
	def __init__(self, config_file: str):
		self._config_data = {}

		self.check_config_file_access(config_file)

		with open(config_file) as stream:
			file_data = yaml.unsafe_load(stream)

		self._config_data = {
			**{"database": {}, "logging": {}, "mqtt": {}},  # default
			**file_data,
		}

		validate(file_data, CONFIG_JSONSCHEMA)

	def get_database_config(self):
		return self._config_data["database"]

	def get_logging_config(self):
		return self._config_data["logging"]

	def get_mqtt_config(self):
		return self._config_data["mqtt"]

	@classmethod
	def check_config_file_access(cls, config_file: str):
		if not os.path.isfile(config_file):
			raise FileNotFoundError(f"config file ({config_file}) does not exist!")

		permissions = oct(os.stat(config_file).st_mode & 0o777)[2:]
		if permissions != "600":
			extra = "change via 'chmod'. this config file may contain sensitive information."
			raise PermissionError(
				f"wrong config file permissions ({config_file}: expected 600, got {permissions})! {extra}"
			)
