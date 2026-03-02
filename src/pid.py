import shelve
import time
from dataclasses import dataclass


@dataclass
class PIDState:
	"""Stores the state of a PID controller."""

	previous_error: float = 0.0
	integral: float = 0.0
	last_time: float = 0.0
	output: float = 0.0


class PIDControllerStore:
	"""A store for PID controllers that persists state using shelve."""

	def __init__(self, shelf_path: str = "pid_state.shelve"):
		self.shelf_path = shelf_path
		self._cache: dict[str, dict[str, PIDState]] = {}

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
		"""
		Compute a new PID output value.

		Args:
		        pid_id: Unique identifier for this PID controller
		        setpoint: The target value
		        process_value: The current value
		        kp: Proportional gain
		        ki: Integral gain
		        kd: Derivative gain
		        min_output: Minimum output value
		        max_output: Maximum output value

		Returns:
		        The calculated output value
		"""
		current_time = time.time()
		error = setpoint - process_value

		# Get the previous state or create a new one
		state = self.get_pid_state(pid_id)

		# Calculate dt (time since last calculation)
		dt = current_time - state.last_time if state.last_time > 0 else 0.1

		# Avoid division by zero or negative time
		if dt <= 0.0:
			dt = 0.1

		# Proportional term
		p_term = kp * error

		# Integral term
		state.integral += error * dt
		i_term = ki * state.integral

		# Derivative term (avoid derivative kick)
		d_term = 0.0
		if dt > 0.0:
			d_term = kd * (error - state.previous_error) / dt

		# Calculate output
		output = p_term + i_term + d_term

		# Apply limits
		output = max(min_output, min(output, max_output))

		# Update state
		state.previous_error = error
		state.last_time = current_time
		state.output = output

		# Save state
		self.save_pid_state(pid_id, state)

		return output

	def reset(self, pid_id: str) -> None:
		"""Reset a PID controller to initial values."""
		self.save_pid_state(pid_id, PIDState())

	def get_last_output(self, pid_id: str) -> float:
		"""Get the last calculated output for a PID controller."""
		return self.get_pid_state(pid_id).output

	def get_pid_state(self, pid_id: str) -> PIDState:
		"""Get the state for a PID controller, loading from disk if needed."""
		# Create a namespace from user ID if not present
		namespace, _, controller_id = pid_id.partition("/")
		if not namespace or not controller_id:
			namespace = "default"
			controller_id = pid_id

		# Check memory cache first
		if namespace in self._cache and controller_id in self._cache[namespace]:
			return self._cache[namespace][controller_id]

		# Load from shelve
		with shelve.open(self.shelf_path) as db:
			if namespace in db and controller_id in db[namespace]:
				# Cache exists
				if namespace not in self._cache:
					self._cache[namespace] = {}
				self._cache[namespace] = db[namespace]
				return self._cache[namespace].get(controller_id, PIDState())
			# No entry exists
			return PIDState()

	def save_pid_state(self, pid_id: str, state: PIDState) -> None:
		"""Save the state for a PID controller to disk."""
		# Create a namespace from user ID if not present
		namespace, _, controller_id = pid_id.partition("/")
		if not namespace or not controller_id:
			namespace = "default"
			controller_id = pid_id

		# Update memory cache
		if namespace not in self._cache:
			self._cache[namespace] = {}
		self._cache[namespace][controller_id] = state

		# Save to shelve
		with shelve.open(self.shelf_path) as db:
			if namespace not in db:
				db[namespace] = {}
			namespace_data = db[namespace]
			namespace_data[controller_id] = state
			db[namespace] = namespace_data

	def get_all_pid_ids(self) -> list[str]:
		"""
		Return a list of all pid_id strings currently stored on disk.
		pid_id is either 'namespace/controller_id' or simply 'controller_id'
		if it lives in the default namespace.
		"""
		ids: list[str] = []

		# Open the shelf and iterate all namespaces
		with shelve.open(self.shelf_path) as db:
			for namespace, controllers in db.items():
				# controllers is itself a dict mapping controller_id -> PIDState
				for controller_id in controllers.keys():
					if namespace == "default":
						ids.append(controller_id)
					else:
						ids.append(f"{namespace}/{controller_id}")

		return ids
