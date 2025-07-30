import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve


async def echo_handler(websocket: ServerConnection) -> None:
	async for message in websocket:
		await websocket.send(message)


@pytest.mark.asyncio  # type: ignore[misc]
async def test_hello() -> None:
	async with serve(echo_handler, "localhost", 8765):
		async with connect("ws://localhost:8765") as websocket:
			await websocket.send("Hello world!")
			message = await websocket.recv()
			assert message == "Hello world!"
