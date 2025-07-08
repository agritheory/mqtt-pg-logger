import logging

import uvicorn
from environs import Env
from quart import Quart
from quart_cors import cors

from src.alarm import Alarm
from src.create_schema import TimescaleDB
from src.gql import graphql_bp
from src.mqtt_logger import MQTTLogger
from src.signals import alarm_refresh_signal

logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger(__name__)


def create_app(**kwargs: str) -> Quart:
	env = Env()
	env.read_env()

	cors_origins = env.list("CORS_ORIGINS", default=["*"])
	app = cors(Quart(__name__), allow_origin=cors_origins)
	# db config
	db_user = (kwargs.get("db_user")) or env.str("DB_USER")
	db_password = (kwargs.get("db_password")) or env.str("DB_PASSWORD")
	db_host = (kwargs.get("db_host")) or env.str("DB_HOST")
	db_port = kwargs.get("db_port") or env.str("DB_PORT", "5432")
	db_name = (kwargs.get("db_name")) or env.str("DB_NAME")
	force_rollback = (kwargs.get("force_rollback")) or env.bool("FORCE_ROLLBACK", False)
	force_rollback = bool(force_rollback)
	app.db = TimescaleDB(
		db_user=db_user,
		db_password=db_password,
		db_host=db_host,
		db_port=db_port,
		db_name=db_name,
		force_rollback=force_rollback,
	)
	app.cache = {}

	@app.before_serving  # type: ignore[misc]
	async def init_database() -> None:
		await app.db.connect()

		if env.bool("CREATE_SCHEMA", True):
			"""Initialize database before serving requests"""
			_logger.info("Initializing database before serving...")
			try:
				from src.create_schema import initialize_db

				# fernet config
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
				_logger.info("Database initialization completed")
			except Exception as e:
				_logger.error(f"Database initialization failed: {e}")
				raise

		# logger and alarms needs the schema to read and write and should be initialized afterwards
		await mqtt_handler()

		alarms = Alarm()
		await alarm_refresh_signal.send_async()

	async def mqtt_handler() -> None:
		broker_url = env.str("MQTT_BROKER_HOST", "localhost")
		broker_port = env.int("MQTT_BROKER_PORT", 1883)
		mqtt_logger = MQTTLogger(app.db, broker_url, broker_port)
		app.add_background_task(mqtt_logger.start)

	app.register_blueprint(graphql_bp, url_prefix="/graphql")

	return app


# Create the application instance at module level
application = create_app()


def main() -> None:
	"""Entry point for the server"""
	env = Env()
	env.read_env()
	_logger.info("Starting MQTT-Quart-Logger server...")
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
