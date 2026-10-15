from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import HTTPException

hasher = PasswordHasher()
DUMMY_HASH = hasher.hash("dummy-password-for-unrecognized-accounts")


def hash_password(password):
    if len(password) < 12 or len(password) > 128:
        raise HTTPException(422, "Use a password between 12 and 128 characters.")
    return hasher.hash(password)


def verify_password(encoded, password):
    try:
        return hasher.verify(encoded or DUMMY_HASH, password) and bool(encoded)
    except (VerificationError, InvalidHashError):
        return False
