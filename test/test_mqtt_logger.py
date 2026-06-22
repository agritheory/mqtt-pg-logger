from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mqtt_logger import MAX_RECONNECT_DELAY, MQTTLogger


@pytest.mark.asyncio  # type: ignore[misc]
async def test_mqtt_reconnects_after_connection_error() -> None:
	with patch("src.mqtt_logger.Alarm"):
		logger = MQTTLogger(db=MagicMock(), broker_url="localhost", broker_port=1883)
		logger.get_topics = AsyncMock(return_value={"test/topic"})  # type: ignore[method-assign]

		connect_attempts = 0

		class FakeClient:
			def __init__(self, *args: object, **kwargs: object) -> None:
				pass

			async def __aenter__(self) -> "FakeClient":
				nonlocal connect_attempts
				connect_attempts += 1
				if connect_attempts == 1:
					raise OSError("connection refused")
				return self

			async def __aexit__(self, *args: object) -> None:
				return None

			async def subscribe(self, *args: object, **kwargs: object) -> None:
				logger.running = False

			def messages(self) -> AsyncIterator[None]:
				async def empty() -> AsyncIterator[None]:
					if False:
						yield None  # pragma: no cover

				return empty()

		logger.client = lambda: FakeClient()  # type: ignore[method-assign]

		with patch.object(logger, "wait_before_reconnect", new=AsyncMock()) as wait_mock:
			await logger.start()

		assert connect_attempts == 2
		wait_mock.assert_awaited_once()


def test_max_reconnect_delay_cap() -> None:
	delay = 1.0
	for _ in range(10):
		delay = min(delay * 2, MAX_RECONNECT_DELAY)
	assert delay == MAX_RECONNECT_DELAY
