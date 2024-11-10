#!/usr/bin/env python3

import datetime
import secrets
from functools import wraps

import jwt  # PyJWT
from environs import Env
from quart import request

token_blacklist = set()

env = Env()


def token_required(func):
	@wraps(func)
	async def wrapper(*args, **kwargs):
		auth_header = request.headers.get("Authorization")
		if not auth_header:
			raise Exception("Authorization header is missing")

		try:
			scheme, token = auth_header.split()
			if scheme.lower() != "bearer":
				raise Exception("Invalid authentication scheme")
		except ValueError:
			raise Exception("Invalid authorization header")

		decoded_token = verify_token(token)
		if not decoded_token:
			raise Exception("Invalid or expired token")

		kwargs["info"].context.user = decoded_token
		return await func(*args, **kwargs)

	return wrapper


def generate_token(username, users, expires_delta=None):
	if expires_delta is None:
		expires_delta = datetime.timedelta(seconds=env.int("ACCESS_TOKEN_EXPIRES"))

	expires = datetime.datetime.now(datetime.UTC) + expires_delta
	token_data = {
		"sub": username,
		"exp": expires,
		"iat": datetime.datetime.now(datetime.UTC),
		"jti": secrets.token_urlsafe(16),
	}

	return jwt.encode(token_data, env.str("JWT_SECRET_KEY"), algorithm="HS256")


def verify_token(token):
	try:
		decoded = jwt.decode(
			token,
			env.str("JWT_SECRET_KEY"),
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
