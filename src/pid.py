import time
from dataclasses import dataclass

import asyncpg
from quart import current_app


@dataclass
class PIDState:
	previous_error: float = 0.0
	integral: float = 0.0
	last_time: float = 0.0
	output: float = 0.0


class PIDControllerStore:
	"""In-memory PID store with Postgres persistence."""

	def __init__(self) -> None:
		self._cache: dict[str, dict[str, PIDState]] = {}
		self._dirty: set[tuple[str, str]] = set()

	def db(self) -> asyncpg.Pool | None:
		return getattr(current_app, "db", None)

	def split_pid_id(self, pid_id: str) -> tuple[str, str]:
		namespace, _, controller_id = pid_id.partition("/")
		if not namespace or not controller_id:
			return "default", pid_id
		return namespace, controller_id

	def compute(
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
		current_time = time.time()
		error = setpoint - process_value
		state = self.get_pid_state(pid_id)
		dt = current_time - state.last_time if state.last_time > 0 else 0.1
		if dt <= 0.0:
			dt = 0.1

		p_term = kp * error
		state.integral += error * dt
		i_term = ki * state.integral
		d_term = 0.0
		if dt > 0.0:
			d_term = kd * (error - state.previous_error) / dt

		output = p_term + i_term + d_term
		output = max(min_output, min(output, max_output))

		state.previous_error = error
		state.last_time = current_time
		state.output = output
		self.save_pid_state(pid_id, state)
		return output

	def reset(self, pid_id: str) -> None:
		self.save_pid_state(pid_id, PIDState())

	def get_last_output(self, pid_id: str) -> float:
		return self.get_pid_state(pid_id).output

	def get_pid_state(self, pid_id: str) -> PIDState:
		namespace, controller_id = self.split_pid_id(pid_id)
		if namespace in self._cache and controller_id in self._cache[namespace]:
			return self._cache[namespace][controller_id]
		return PIDState()

	def save_pid_state(self, pid_id: str, state: PIDState) -> None:
		namespace, controller_id = self.split_pid_id(pid_id)
		if namespace not in self._cache:
			self._cache[namespace] = {}
		self._cache[namespace][controller_id] = state
		self._dirty.add((namespace, controller_id))

	async def load_all_from_db(self) -> None:
		db = self.db()
		if db is None:
			return
		rows = await db.fetch(
			"""
			SELECT namespace, controller_id, previous_error, integral, last_time, output
			FROM pid_state
			"""
		)
		for row in rows:
			namespace = row["namespace"]
			controller_id = row["controller_id"]
			if namespace not in self._cache:
				self._cache[namespace] = {}
			self._cache[namespace][controller_id] = PIDState(
				previous_error=row["previous_error"],
				integral=row["integral"],
				last_time=row["last_time"],
				output=row["output"],
			)

	async def flush_dirty(self) -> None:
		db = self.db()
		if db is None or not self._dirty:
			return
		for namespace, controller_id in list(self._dirty):
			state = self._cache.get(namespace, {}).get(controller_id)
			if state is None:
				continue
			await db.execute(
				"""
				INSERT INTO pid_state (
					namespace, controller_id, previous_error, integral, last_time, output
				)
				VALUES ($1, $2, $3, $4, $5, $6)
				ON CONFLICT (namespace, controller_id) DO UPDATE SET
					previous_error = EXCLUDED.previous_error,
					integral = EXCLUDED.integral,
					last_time = EXCLUDED.last_time,
					output = EXCLUDED.output
				""",
				namespace,
				controller_id,
				state.previous_error,
				state.integral,
				state.last_time,
				state.output,
			)
		self._dirty.clear()

	async def get_all_pid_ids(self) -> list[str]:
		db = self.db()
		if db is None:
			ids: list[str] = []
			for namespace, controllers in self._cache.items():
				for controller_id in controllers:
					if namespace == "default":
						ids.append(controller_id)
					else:
						ids.append(f"{namespace}/{controller_id}")
			return ids

		rows = await db.fetch(
			"SELECT namespace, controller_id FROM pid_state ORDER BY namespace, controller_id"
		)
		result: list[str] = []
		for row in rows:
			if row["namespace"] == "default":
				result.append(row["controller_id"])
			else:
				result.append(f"{row['namespace']}/{row['controller_id']}")
		return result
