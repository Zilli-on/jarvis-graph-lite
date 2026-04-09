"""Greeting service — depends on helpers."""

from helpers import format_greeting


class GreetingService:
    """Builds greeting messages from a config dict."""

    def __init__(self, config: dict) -> None:
        self.prefix = config.get("prefix", "Hello")

    def greet(self, name: str) -> str:
        return format_greeting(f"{self.prefix}, {name}")

    def shout(self, name: str) -> str:
        return self.greet(name).upper()
