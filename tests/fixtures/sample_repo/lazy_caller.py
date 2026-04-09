"""Demonstrates a function-local import — common in real codebases to
avoid circular imports or for lazy loading."""


def call_lazily() -> str:
    # Import inside the function body. The parser must still see this.
    from helpers import format_greeting
    return format_greeting("lazy")
