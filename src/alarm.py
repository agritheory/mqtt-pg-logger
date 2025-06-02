import logging
from collections import defaultdict
from dataclasses import dataclass
from types import CodeType
from typing import Any

from blinker import signal
from quart import current_app
from RestrictedPython import compile_restricted
from RestrictedPython.Guards import safe_builtins, safe_globals

from src.pid import PIDControllerStore

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
	byte_code: CodeType  # Compiled Python code object


class Alarm:
	def __init__(self, cache: dict | None = None, pid_store_path: str = "pid.shelve"):
		self.cache = {} if cache is None else cache
		self.topic_mapping: dict[str, set[int]] = defaultdict(set)
		self.pid_store = PIDControllerStore(pid_store_path)

		# Set up safe globals for RestrictedPython
		self.safe_globals = dict(safe_globals)
		self.safe_globals.update(safe_builtins)

		# Add PID functions to safe_globals
		self.safe_globals.update(
			{
				"pid_compute": self.pid_compute,
				"pid_reset": self.pid_reset,
				"pid_last_output": self.pid_last_output,
			}
		)

		# Register blinker signal handler
		self.alarm_signal = signal("alarm")
		self.alarm_signal.connect(self.handle_message)

		self.alarm_refresh_signal = signal("refresh_alarms")
		self.alarm_refresh_signal.connect(self.load_alarms)

	# PID wrapper functions to expose to user alarm code
	def pid_compute(
		self,
		pid_id: str,
		setpoint: float,
		process_value: float,
		kp: float = 1.0,
		ki: float = 0.0,
		kd: float = 0.0,
		min_output: float = float("-inf"),
		max_output: float = float("inf"),
	) -> float:
		"""Safe wrapper for PID computation."""
		try:
			return self.pid_store.compute(
				pid_id, setpoint, process_value, kp, ki, kd, min_output, max_output
			)
		except Exception as e:
			_logger.error(f"Error in PID compute for {pid_id}: {str(e)}")
			return 0.0

	def pid_reset(self, pid_id: str) -> None:
		"""Safe wrapper for PID reset."""
		try:
			self.pid_store.reset(pid_id)
		except Exception as e:
			_logger.error(f"Error in PID reset for {pid_id}: {str(e)}")

	def pid_last_output(self, pid_id: str) -> float:
		"""Safe wrapper to get the last PID output."""
		try:
			return self.pid_store.get_last_output(pid_id)
		except Exception as e:
			_logger.error(f"Error getting last PID output for {pid_id}: {str(e)}")
			return 0.0

	async def load_alarms(self) -> None:
		query = """
			SELECT id, condition, owner, topic, alarm_name, delivery_method, disabled
			FROM alarm
			WHERE disabled = FALSE
		"""

		rows = await current_app.db.fetch_all(query=query)
		self.topic_mapping.clear()
		current_ids = set()

		for row in rows:
			alarm_id = int(row["id"])
			current_ids.add(alarm_id)

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

			self.cache[alarm_id] = cached_alarm
			self.topic_mapping[row["topic"]].add(alarm_id)

		# Remove stale entries
		stale_keys = set(self.cache.keys()) - {str(id) for id in current_ids}
		for key in stale_keys:
			del self.cache[key]

	async def handle_message(self, sender: str, **kwargs: str) -> None:
		topic = kwargs.get("topic")
		if not topic:
			return

		message_data: str | dict[str, Any] = kwargs.get("message", {})
		matching_alarm_ids = self.topic_mapping.get(topic, set())

		if not matching_alarm_ids:
			return

		for alarm_id in matching_alarm_ids:
			try:
				alarm = self.cache.get(str(alarm_id))
				if not alarm:
					continue

				# Create restricted environment with message data
				locals_dict = {"message": message_data}

				# Evaluate the pre-compiled condition
				result = eval(alarm.byte_code, self.safe_globals, locals_dict)
				# filter/forward code here

				if result:
					self.trigger_alarm(alarm, message_data)

			except Exception as e:
				_logger.error(f"Error processing alarm {alarm_id}: {str(e)}")
				continue

	def trigger_alarm(self, alarm: CompiledAlarm, message_data: str | dict[str, Any]) -> None:
		# Replace this with your notification implementation
		_logger.error(f"ALARM TRIGGERED: {alarm.alarm_name} on topic {alarm.topic}")
		_logger.error("Alarm Notifications are not yet implemented")

	def get_all_pid_ids(self) -> list[str]:
		return self.pid_store.get_all_pid_ids()
