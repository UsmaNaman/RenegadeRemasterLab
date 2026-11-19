"""Logging: concise messages on stderr, full detail in <workspace>/logs/rrl.log."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path


@contextmanager
def setup_logging(log_file: Path | None, verbose: bool = False) -> Iterator[None]:
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    handlers: list[logging.Handler] = [console]

    if log_file is not None:
        file = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        file.setLevel(logging.DEBUG)
        file.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handlers.append(file)

    root = logging.getLogger()
    previous_handlers, previous_level = root.handlers[:], root.level
    root.handlers[:] = handlers
    root.setLevel(logging.DEBUG)
    try:
        yield
    finally:
        root.handlers[:] = previous_handlers
        root.setLevel(previous_level)
        for handler in handlers:
            handler.close()
