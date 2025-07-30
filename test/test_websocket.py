import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import Server


@pytest.mark.asyncio  # type: ignore[misc]
async def test_hello(websocket_server: Server) -> None:
	async with connect("ws://localhost:8765") as websocket:
		await websocket.send("Hello world!")
		message = await websocket.recv()
		assert message == "Hello world!"
