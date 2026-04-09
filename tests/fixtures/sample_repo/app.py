"""Top-level app — wires service + helpers together."""

from helpers import format_greeting, load_config
from service import GreetingService
from package.worker import run_worker

VERSION = "1.0.0"


def main() -> int:
    cfg = load_config()
    svc = GreetingService(cfg)
    msg = svc.greet("world")
    print(format_greeting(msg))
    run_worker()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
