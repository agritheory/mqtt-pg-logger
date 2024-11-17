# src/server.py
import logging

import uvicorn
from environs import Env
from quart import Quart
from quart_cors import cors

from src.alarm import Alarm
from src.create_schema import TimescaleDB
from src.gql import graphql_bp
from src.mqtt_logger import MQTTLogger

logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger(__name__)

env = Env()
env.read_env()


def create_app() -> Quart:
	app = Quart(__name__)
	app.db = TimescaleDB()
	cors_origins = env.list("CORS_ORIGINS", default=["*"])
	app = cors(app, allow_origin=cors_origins)

	@app.before_serving
	async def init_database():
		await app.db.connect()

		if env.bool("CREATE_SCHEMA", True):
			"""Initialize database before serving requests"""
			_logger.info("Initializing database before serving...")
			try:
				from src.create_schema import initialize_db

				await initialize_db()
				_logger.info("Database initialization completed")
			except Exception as e:
				_logger.error(f"Database initialization failed: {e}")
				raise

		# logger and alarms needs the schema to read and write and should be initialized afterwards
		await mqtt_handler()

		alarms = Alarm()
		await alarms.load_alarms()

	async def mqtt_handler():
		broker_url = env.str("MQTT_BROKER_HOST", "localhost")
		broker_port = env.int("MQTT_BROKER_PORT", 1883)
		mqtt_logger = MQTTLogger(app.db, broker_url, broker_port)
		app.add_background_task(mqtt_logger.start)

	app.register_blueprint(graphql_bp, url_prefix="/graphql")

	return app


# Create the application instance at module level
application = create_app()


def main():
	"""Entry point for the server"""
	_logger.info("Starting MQTT-Quart-Logger server...")
	host = env.str("HOST", "0.0.0.0")
	port = env.int("PORT", 8000)
	uvicorn.run(
		"src.server:application",
		host=host,
		port=port,
		reload=env.bool("DEBUG", True),
		log_level="debug" if env.bool("DEBUG", True) else "info",
		workers=env.int("UVICORN_WORKERS", 1),
	)


if __name__ == "__main__":
	main()
