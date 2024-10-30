import logging
import logging.handlers
import os
import sys


class AppLogging:
	@classmethod
	def configure(
		cls,
		config_data: dict,
		log_file: str,
		log_level: str | int,
		print_logs: bool,
		systemd_mode: bool,
	):
		handlers = []

		if not log_file:
			log_file = config_data.get("log_file")

		if not log_level:
			log_level = config_data.get("log_level")
		log_level = cls.parse_log_level(log_level)

		if print_logs is None:
			print_logs = config_data.get("print_logs", False)
		if systemd_mode is None:
			systemd_mode = config_data.get("systemd_mode", False)

		format_with_ts = "%(asctime)s [%(levelname)8s] %(name)s: %(message)s"
		format_no_ts = "[%(levelname)8s] %(name)s: %(message)s"

		if log_file:
			log_dir = os.path.dirname(log_file)
			if not os.path.exists(log_dir):
				os.makedirs(log_dir, exist_ok=True)

			max_bytes = config_data.get("max_bytes", 1048576)
			max_count = config_data.get("max_count", 5)
			handler = logging.handlers.RotatingFileHandler(
				log_file, maxBytes=int(max_bytes), backupCount=int(max_count)
			)
			formatter = logging.Formatter(format_with_ts)
			handler.setFormatter(formatter)
			handlers.append(handler)

		if systemd_mode:
			log_format = format_no_ts
		else:
			log_format = format_with_ts

		if print_logs or systemd_mode:
			handlers.append(logging.StreamHandler(sys.stdout))

		logging.basicConfig(format=log_format, level=log_level, handlers=handlers)

	@classmethod
	def parse_log_level(cls, value: str | int = logging.INFO):
		log_level = logging.INFO
		if isinstance(value, int):
			level_exists = logging._levelToName.get(value)
			log_level = value if level_exists else logging.INFO
		elif isinstance(value, str):
			upper_value = value.strip().upper()
			level = logging._nameToLevel.get(upper_value)
			log_level = level if level else logging.INFO
		return log_level
