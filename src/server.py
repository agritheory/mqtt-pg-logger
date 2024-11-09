import datetime
import logging
import secrets

from environs import Env
from quart import Quart
from quart_auth import QuartAuth
from quart_cors import cors

from src.auth import auth_bp
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
	app.config.update(
		SECRET_KEY=env.str("SECRET_KEY", default=secrets.token_urlsafe(32)),
		JWT_SECRET_KEY=env.str("JWT_SECRET_KEY", default=secrets.token_urlsafe(32)),
		PORT=env.int("PORT", default=5000),
		ACCESS_TOKEN_EXPIRES=env.int("ACCESS_TOKEN_EXPIRES", default=3600),
		REFRESH_TOKEN_EXPIRES=env.int("REFRESH_TOKEN_EXPIRES", default=2592000),
	)

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

	@app.route("/health")
	async def health_check():
		health_status = {
			"status": "ok",
			"timestamp": datetime.datetime.utcnow().isoformat(),
			"components": {"database": "ok", "mqtt": "ok"},
		}

		if hasattr(app, "db"):
			try:
				# Check database connection
				await app.db.execute("SELECT 1")
			except Exception as e:
				health_status["status"] = "error"
				health_status["components"]["database"] = str(e)

		# Check MQTT connection if available
		if hasattr(app, "mqtt_logger"):
			if not app.mqtt_logger.client.is_connected():
				health_status["status"] = "error"
				health_status["components"]["mqtt"] = "disconnected"

		status_code = 200 if health_status["status"] == "ok" else 503
		return health_status, status_code

	app.register_blueprint(auth_bp, url_prefix="/")
	app.register_blueprint(graphql_bp, url_prefix="/graphql")

	return app


def main():
	"""Entry point for the server"""
	_logger.info("Starting MQTT-Quart-Logger server...")
	env = Env()
	env.read_env()
	app = create_app()
	app.run(host=env.str("HOST"), port=env.int("PORT"))


# TODO:
# - Quart Auth w/ token workflow - probably need to replace columns in user table
# -
