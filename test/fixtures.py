import asyncio

from aiomqtt import Client


async def publish_temperature():
	async with Client("localhost:1883") as client:
		await client.publish("temperature/outside", payload=28.4)
		asyncio.sleep(1)


if __name__ == "__main__":
	asyncio.run(publish_temperature())
