"""Worker — uses helpers and is called by app."""

from helpers import format_greeting


def run_worker() -> str:
    msg = format_greeting("worker started")
    return msg
