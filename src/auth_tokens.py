import datetime
import time
from collections import defaultdict

import asyncpg


class LoginRateLimiter:
	"""In-memory sliding-window rate limiter for login attempts."""

	def __init__(self, max_attempts: int = 10, window_seconds: float = 300.0):
		self.max_attempts = max_attempts
		self.window_seconds = window_seconds
		self.attempts: dict[str, list[float]] = defaultdict(list)

	def is_blocked(self, key: str) -> bool:
		now = time.monotonic()
		window_start = now - self.window_seconds
		attempts = [t for t in self.attempts[key] if t >= window_start]
		self.attempts[key] = attempts
		return len(attempts) >= self.max_attempts

	def record_failure(self, key: str) -> None:
		self.attempts[key].append(time.monotonic())

	def clear(self, key: str) -> None:
		self.attempts.pop(key, None)


login_rate_limiter = LoginRateLimiter()


async def revoke_token(
	db: asyncpg.Pool | asyncpg.Connection,
	jti: str,
	expires_at: datetime.datetime,
) -> None:
	await db.execute(
		"""
		INSERT INTO revoked_token (jti, expires_at)
		VALUES ($1, $2)
		ON CONFLICT (jti) DO NOTHING
		""",
		jti,
		expires_at,
	)


async def is_token_revoked(db: asyncpg.Pool | asyncpg.Connection, jti: str) -> bool:
	row = await db.fetchrow(
		"SELECT 1 FROM revoked_token WHERE jti = $1 AND expires_at > NOW()",
		jti,
	)
	return row is not None
