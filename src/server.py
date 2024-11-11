import logging

from environs import Env
from quart import Quart
from quart_auth import QuartAuth
from quart_cors import cors

from src.create_schema import TimescaleDB
from src.gql import graphql_bp
from src.mqtt_logger import MQTTLogger

logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger(__name__)

env = Env()
env.read_env()


def create_app() -> Quart:
	app = Quart(__name__)
	auth = QuartAuth(app)
	app.db = TimescaleDB()
	cors_origins = env.list("CORS_ORIGINS", default=["*"])
	app = cors(app, allow_origin=cors_origins)
	app.auth_manager = QuartAuth(app)

	@app.before_serving
	async def init_database():
		await app.db.connect()
		await mqtt_handler()
		if env.bool("CREATE_SCHEMA", False):
			"""Initialize database before serving requests"""
			_logger.info("Initializing database before serving...")
			try:
				from src.create_schema import initialize_db

				await initialize_db()
				_logger.info("Database initialization completed")
			except Exception as e:
				_logger.error(f"Database initialization failed: {e}")
				raise

	async def mqtt_handler():
		broker_url = env.str("MQTT_BROKER_HOST", "localhost")
		broker_port = env.int("MQTT_BROKER_PORT", 1883)
		mqtt_logger = MQTTLogger(app.db, broker_url, broker_port)

		app.add_background_task(mqtt_logger.start)

	app.register_blueprint(graphql_bp, url_prefix="/graphql")

	return app


def main():
	"""Entry point for the server"""
	_logger.info("Starting MQTT-Quart-Logger server...")
	env = Env()
	env.read_env()
	app = create_app()
	app.run(host=env.str("HOST"), port=env.int("PORT"), debug=True)
