import secrets

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
CODE_LENGTH = 7


def generate_code(length: int = CODE_LENGTH) -> str:
    """Return a cryptographically random Base62 code."""

    return "".join(secrets.choice(ALPHABET) for _ in range(length))
