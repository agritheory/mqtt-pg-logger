import base64
from typing import cast

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
	return cast(str, password_hasher.hash(password))


def verify_argon2(stored_hash: str, password: str) -> bool:
	try:
		return cast(bool, password_hasher.verify(stored_hash, password))
	except VerifyMismatchError:
		return False


def is_argon2_hash(stored_hash: str) -> bool:
	return stored_hash.startswith("$argon2")


def is_fernet_legacy_hash(stored_hash: str) -> bool:
	if stored_hash.startswith("gAAAA"):
		return True
	try:
		decoded = base64.b64decode(stored_hash, validate=True)
	except Exception:
		return False
	return decoded.startswith(b"gAAAA")


def verify_fernet_legacy(stored_hash: str, password: str, fernet_key: str | None) -> bool:
	if not fernet_key:
		return False
	try:
		if stored_hash.startswith("gAAAA"):
			ciphertext = stored_hash.encode("utf-8")
		else:
			ciphertext = base64.b64decode(stored_hash)
		fernet = Fernet(fernet_key.encode() if isinstance(fernet_key, str) else fernet_key)
		plaintext: str = fernet.decrypt(ciphertext).decode()
		return plaintext == password
	except (InvalidToken, ValueError, TypeError):
		return False


def verify_password(
	stored_hash: str | None,
	password: str,
	*,
	fernet_key: str | None = None,
) -> bool:
	if not stored_hash:
		return False
	if is_argon2_hash(stored_hash):
		return verify_argon2(stored_hash, password)
	if is_fernet_legacy_hash(stored_hash):
		return verify_fernet_legacy(stored_hash, password, fernet_key)
	return False
