#!/usr/bin/env python3

import datetime
import secrets
from functools import wraps

import jwt  # PyJWT
from quart import Blueprint, current_app, jsonify, request
from quart_auth import AuthUser

auth_bp = Blueprint("auth", __name__)
token_blacklist = set()


def token_required(f):
	@wraps(f)
	async def decorated_function(*args, **kwargs):
		token = None
		auth_header = request.headers.get("Authorization")

		if auth_header:
			try:
				scheme, token = auth_header.split()
				if scheme.lower() != "bearer":
					return jsonify({"error": "Invalid authentication scheme"}), 401
			except ValueError:
				return jsonify({"error": "Invalid authorization header"}), 401

		if not token:
			return jsonify({"error": "Token is missing"}), 401

		decoded_token = verify_token(token)
		if not decoded_token:
			return jsonify({"error": "Invalid or expired token"}), 401

		request.token_data = decoded_token
		return await f(*args, **kwargs)

	return decorated_function


def generate_token(username, users, expires_delta=None):
	if expires_delta is None:
		expires_delta = datetime.timedelta(seconds=current_app.config["ACCESS_TOKEN_EXPIRES"])

	expires = datetime.datetime.now(datetime.UTC) + expires_delta
	token_data = {
		"sub": username,
		"exp": expires,
		"iat": datetime.datetime.now(datetime.UTC),
		"jti": secrets.token_urlsafe(16),
	}
	return jwt.encode(token_data, current_app.config["JWT_SECRET_KEY"], algorithm="HS256")


def verify_token(token):
	try:
		decoded = jwt.decode(
			token,
			current_app.config["JWT_SECRET_KEY"],
			algorithms=["HS256"],
			options={"verify_exp": True},
		)
		if decoded["jti"] in token_blacklist:
			return None
		return decoded
	except jwt.ExpiredSignatureError:
		return None
	except jwt.InvalidTokenError:
		return None


@auth_bp.route("/login", methods=["POST"])
async def login():
	try:
		data = await request.get_json()
		if not data:
			return jsonify({"error": "No JSON data provided"}), 400

		username = data.get("username")
		password = data.get("password")

		if not username or not password:
			return jsonify({"error": "Missing credentials"}), 400

		users = current_app.config["USERS"]
		if username in users and users[username]["password"] == password:
			access_token = generate_token(username, users)
			refresh_token = generate_token(
				username,
				users,
				expires_delta=datetime.timedelta(seconds=current_app.config["REFRESH_TOKEN_EXPIRES"]),
			)

			current_app.auth_manager.login_user(AuthUser(username))

			return jsonify(
				{
					"message": "Login successful",
					"access_token": access_token,
					"refresh_token": refresh_token,
					"token_type": "bearer",
					"expires_in": current_app.config["ACCESS_TOKEN_EXPIRES"],
				}
			)

		return jsonify({"error": "Invalid credentials"}), 401

	except Exception as e:
		print(f"Login error: {str(e)}")
		return jsonify({"error": f"Server error: {str(e)}"}), 500


@auth_bp.route("/refresh", methods=["POST"])
@token_required
async def refresh_token():
	username = request.token_data["sub"]
	users = current_app.config["USERS"]
	new_access_token = generate_token(username, users)

	return jsonify(
		{
			"access_token": new_access_token,
			"token_type": "bearer",
			"expires_in": current_app.config["ACCESS_TOKEN_EXPIRES"],
		}
	)


@auth_bp.route("/logout", methods=["POST"])
@token_required
async def logout():
	token_blacklist.add(request.token_data["jti"])
	current_app.auth_manager.logout_user()
	return jsonify({"message": "Successfully logged out"})
