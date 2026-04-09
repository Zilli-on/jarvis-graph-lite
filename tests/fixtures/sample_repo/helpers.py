"""Pure helpers — no dependencies on the rest of the repo."""

DEFAULT_PREFIX = "Hi"


def load_config() -> dict:
    return {"prefix": DEFAULT_PREFIX}


def format_greeting(text: str) -> str:
    return f"[greeting] {text}"
