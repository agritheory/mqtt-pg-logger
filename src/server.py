import logging

import uvicorn
from environs import Env
from quart import Quart
from quart_cors import cors

from src.alarm import Alarm
from src.create_schema import create_pool, initialize_db
from src.gql import graphql_bp
from src.mqtt_logger import MQTTLogger
from src.signals import alarm_refresh_signal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_app(**kwargs: str) -> Quart:
	env = Env()
	env.read_env()

	cors_origins = env.list("CORS_ORIGINS", default=["*"])
	app = cors(Quart(__name__), allow_origin=cors_origins)

	app.db = None
	app.cache = {}

	@app.before_serving  # type: ignore[misc]
	async def init_database() -> None:
		# DB URL is resolved here, not in create_app(), so importing this module
		# and calling create_app() is safe without any database env vars set.
		db_url = kwargs.get("db_url") or env.str("DB_URL", None)
		if not db_url:
			db_user = kwargs.get("db_user") or env.str("DB_USER")
			db_password = kwargs.get("db_password") or env.str("DB_PASSWORD")
			db_host = kwargs.get("db_host") or env.str("DB_HOST")
			db_port = kwargs.get("db_port") or env.str("DB_PORT", "5432")
			db_name = kwargs.get("db_name") or env.str("DB_NAME")
			db_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

		app.db = await create_pool(db_url)

		if env.bool("CREATE_SCHEMA", True):
			logger.info("Initializing database before serving...")
			try:
				fernet_key = env.str("FERNET_KEY", None)
				admin_email = env.str("ADMIN_EMAIL", None)
				admin_password = env.str("ADMIN_PASSWORD", None)
				mqtt_user = env.str("MQTT_USER")
				await initialize_db(
					app.db,
					fernet_key=fernet_key,
					admin_email=admin_email,
					admin_password=admin_password,
					mqtt_user=mqtt_user,
				)
				logger.info("Database initialization completed")
			except Exception as e:
				logger.error(f"Database initialization failed: {e}")
				raise

		await mqtt_handler()

		Alarm()
		await alarm_refresh_signal.send_async()

	@app.after_serving  # type: ignore[misc]
	async def close_database() -> None:
		if app.db is not None:
			await app.db.close()

	async def mqtt_handler() -> None:
		broker_url = env.str("MQTT_BROKER_HOST", "localhost")
		broker_port = env.int("MQTT_BROKER_PORT", 1883)
		mqttlogger = MQTTLogger(app.db, broker_url, broker_port)
		app.mqtt_logger = mqttlogger
		app.add_background_task(mqttlogger.start)

	app.register_blueprint(graphql_bp, url_prefix="/graphql")

	return app


# Create the application instance at module level
application = create_app()


def main() -> None:
	"""Entry point for the server"""
	env = Env()
	env.read_env()
	logger.info("Starting MQTT-Quart-Logger server...")
	host = env.str("HOST", "0.0.0.0")
	port = env.int("PORT", 5000)
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
