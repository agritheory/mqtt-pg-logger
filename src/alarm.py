import logging
import shelve
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from blinker import signal
from quart import current_app
from RestrictedPython import compile_restricted
from RestrictedPython.Guards import safe_builtins, safe_globals

_logger = logging.getLogger(__name__)


@dataclass
class CompiledAlarm:
	id: int
	condition: str
	topic: str
	alarm_name: str
	delivery_method: str
	owner: str
	disabled: bool
	byte_code: Any  # Compiled Python code object


class Alarm:
	def __init__(self, cache_path: str = "alarms.shelve"):
		self.cache_path = Path(cache_path)
		self.topic_mapping: dict[str, set[int]] = defaultdict(set)
		self.safe_globals = dict(safe_globals)
		self.safe_globals.update(safe_builtins)

		# Register blinker signal handler
		self.alarm_signal = signal("alarm")
		self.alarm_signal.connect(self.handle_message)

		self.alarm_refresh_signal = signal("refresh_alarms")
		self.alarm_refresh_signal.connect(self.load_alarms)

	async def load_alarms(self) -> None:
		query = """
			SELECT id, condition, owner, topic, alarm_name, delivery_method, disabled
			FROM alarms
			WHERE disabled = FALSE
		"""

		try:
			rows = await current_app.db.fetch_all(query=query)
			self.topic_mapping.clear()
			with shelve.open(str(self.cache_path)) as cache:
				current_ids = set()

				for row in rows:
					alarm_id = row["id"]
					current_ids.add(alarm_id)

					try:
						byte_code = compile_restricted(row["condition"], "<string>", "eval")

						cached_alarm = CompiledAlarm(
							id=alarm_id,
							condition=row["condition"],
							topic=row["topic"],
							alarm_name=row["alarm_name"],
							delivery_method=row["delivery_method"],
							owner=row["owner"],
							disabled=row["disabled"],
							byte_code=byte_code,
						)

						cache[str(alarm_id)] = cached_alarm
						self.topic_mapping[row["topic"]].add(alarm_id)

					except Exception as e:
						_logger.error(f"Error compiling alarm {alarm_id}: {str(e)}")
						continue

				# Remove stale entries
				stale_keys = set(cache.keys()) - {str(id) for id in current_ids}
				for key in stale_keys:
					del cache[key]

		except Exception as e:
			_logger.error(f"Error loading alarms: {str(e)}")

	async def handle_message(self, sender, **kwargs):
		topic = kwargs.get("topic")
		if not topic:
			return

		message_data = kwargs.get("message", {})
		matching_alarm_ids = self.topic_mapping.get(topic, set())

		if not matching_alarm_ids:
			return

		with shelve.open(str(self.cache_path)) as cache:
			for alarm_id in matching_alarm_ids:
				try:
					alarm = cache.get(str(alarm_id))
					if not alarm:
						continue

					# Create restricted environment with message data
					locals_dict = {"message": message_data}

					# Evaluate the pre-compiled condition
					result = eval(alarm.byte_code, self.safe_globals, locals_dict)

					if result:
						await self.trigger_alarm(alarm, message_data)

				except Exception as e:
					_logger.error(f"Error processing alarm {alarm_id}: {str(e)}")
					continue
