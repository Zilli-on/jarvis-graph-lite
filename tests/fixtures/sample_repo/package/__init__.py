"""Package init — re-exports the worker entrypoint."""

from package.worker import run_worker

__all__ = ["run_worker"]
