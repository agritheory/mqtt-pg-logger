import asyncio

from aiomqtt import Client, MqttError


async def publish_temperature():
	# split host & port
	async with Client(
		hostname="artemis",  # from *inside* your app container
		port=1883,
		username="artemis",
		password="artemis",
	) as client:
		await client.publish("temperature/outside", payload=b"28.4")
		# give the library a chance to flush
		await asyncio.sleep(1)


if __name__ == "__main__":
	try:
		asyncio.run(publish_temperature())
		print("✅ Published OK")
	except MqttError as e:
		print(f"❌ MQTT error: {e}")
