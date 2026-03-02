import json
import logging
from collections import defaultdict
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from types import CodeType
from typing import Any

import aiomqtt
from quart import current_app
from RestrictedPython import compile_restricted
from RestrictedPython.Guards import safe_builtins, safe_globals

from src.pid import PIDControllerStore
from src.signals import alarm_refresh_signal, alarm_triggered
from src.webhook_delivery import ALARM_TRIGGERED_TYPE, deliver_standard_webhook

logger = logging.getLogger(__name__)


@dataclass
class WebhookTarget:
	"""Target for Standard Webhooks delivery (from webhook table)."""

	url: str
	signing_secret: str


PublishCallback = Callable[[str, str], Coroutine[Any, Any, None]]


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
	webhook_target: WebhookTarget | None = None
	forward_topic: str | None = None


class Alarm:
	def __init__(
		self,
		cache: dict | None = None,
		pid_store_path: str = "pid.shelve",
		publish_callback: PublishCallback | None = None,
	):
		self.cache = current_app.cache if cache is None else cache
		self.topic_mapping: dict[str, set[int]] = defaultdict(set)
		self.pid_store = PIDControllerStore(pid_store_path)
		self.publish_callback = publish_callback

		# Set up safe globals for RestrictedPython
		self.safe_globals = dict(safe_globals)
		self.safe_globals.update(safe_builtins)

		# Add PID functions to safe_globals
		self.safe_globals.update(
			{
				"pid_compute": self.pid_compute,
				"pid_reset": self.pid_reset,
				"pid_last_output": self.pid_last_output,
				"_getitem_": lambda obj, key: obj[key],
			}
		)
		self.alarm_refresh_signal = alarm_refresh_signal
		self.alarm_refresh_signal.connect(self.load_alarms)

		self.alarm_triggered = alarm_triggered

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
			logger.error(f"Error in PID compute for {pid_id}: {str(e)}")
			return 0.0

	def pid_reset(self, pid_id: str) -> None:
		"""Safe wrapper for PID reset."""
		try:
			self.pid_store.reset(pid_id)
		except Exception as e:
			logger.error(f"Error in PID reset for {pid_id}: {str(e)}")

	def pid_last_output(self, pid_id: str) -> float:
		"""Safe wrapper to get the last PID output."""
		try:
			return self.pid_store.get_last_output(pid_id)
		except Exception as e:
			logger.error(f"Error getting last PID output for {pid_id}: {str(e)}")
			return 0.0

	async def load_alarms(self, sender: Any) -> None:
		query = """
			SELECT a.id, a.condition, a.owner, a.topic, a.alarm_name, a.delivery_method, a.disabled,
			       a.forward_topic, w.url AS webhook_table_url, w.signing_secret
			FROM alarm a
			LEFT JOIN webhook w ON a.webhook_id = w.id AND w.disabled = FALSE
			WHERE a.disabled = FALSE
		"""
		rows = await current_app.db.fetch(query)
		self.topic_mapping.clear()
		current_ids = set()

		for row in rows:
			r = dict(row)
			alarm_id = int(r["id"])
			current_ids.add(alarm_id)

			byte_code = compile_restricted(r["condition"], "<string>", "eval")

			webhook_target: WebhookTarget | None = None
			if r.get("webhook_table_url") and r.get("signing_secret"):
				webhook_target = WebhookTarget(
					url=r["webhook_table_url"],
					signing_secret=r["signing_secret"],
				)

			cached_alarm = CompiledAlarm(
				id=alarm_id,
				condition=r["condition"],
				topic=r["topic"],
				alarm_name=r["alarm_name"],
				delivery_method=r["delivery_method"],
				owner=r["owner"],
				disabled=r["disabled"],
				byte_code=byte_code,
				webhook_target=webhook_target,
				forward_topic=r.get("forward_topic"),
			)

			self.cache[alarm_id] = cached_alarm
			self.topic_mapping[r["topic"]].add(alarm_id)

		# Remove stale entries (compare int keys consistently)
		stale_keys = set(self.cache.keys()) - current_ids
		for key in stale_keys:
			del self.cache[key]

	async def handle_message(self, message: aiomqtt.Message) -> None:
		logger.info(f"Handling message: {message.topic}")

		message_data: str | dict[str, Any] = message.payload.decode()
		matching_alarm_ids = self.topic_mapping.get(str(message.topic), set())
		logger.info(f"possible alarm ids: {self.topic_mapping.keys()}")
		logger.info(f"Matching alarm ids: {matching_alarm_ids}")

		if not matching_alarm_ids:
			logger.info(f"No matching alarms for topic: {message.topic}")
			return

		for alarm_id in matching_alarm_ids:
			try:
				alarm = self.cache.get(int(alarm_id))
				if not alarm:
					logger.info(f"Alarm {alarm_id} not found in cache")
					continue

				# Create restricted environment with message data
				if isinstance(message_data, str):
					message_data = json.loads(message_data)
				locals_dict = {"message": message_data}

				# Evaluate the pre-compiled condition
				result = eval(alarm.byte_code, self.safe_globals, locals_dict)

				if result:
					await self.trigger_alarm(alarm, message_data)

			except Exception as e:
				logger.error(f"Error processing alarm {alarm_id}: {str(e)}")
				continue

	async def trigger_alarm(self, alarm: CompiledAlarm, message_data: str | dict[str, Any]) -> None:
		logger.info(f"ALARM TRIGGERED: {alarm.alarm_name} on topic {alarm.topic}")
		self.alarm_triggered.send(self, alarm=alarm, message_data=message_data)

		if alarm.delivery_method == "mqtt":
			if self.publish_callback and alarm.forward_topic:
				payload = message_data if isinstance(message_data, str) else json.dumps(message_data)
				await self.publish_callback(alarm.forward_topic, payload)
			else:
				logger.warning(
					f"delivery_method='mqtt' but no callback/forward_topic for '{alarm.alarm_name}'"
				)
		elif alarm.webhook_target:
			data = {
				"alarm_id": alarm.id,
				"alarm_name": alarm.alarm_name,
				"topic": alarm.topic,
				"condition": alarm.condition,
				"message_data": message_data,
			}
			await deliver_standard_webhook(
				alarm.webhook_target.url,
				alarm.webhook_target.signing_secret,
				ALARM_TRIGGERED_TYPE,
				data,
			)
		else:
			logger.info(f"No delivery configured for alarm '{alarm.alarm_name}'; skipping delivery")

	def get_all_pid_ids(self) -> list[str]:
		return self.pid_store.get_all_pid_ids()
