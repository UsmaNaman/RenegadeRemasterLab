"""Renegade Remaster Lab: preserve, enhance, reimagine."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("renegade-remaster-lab")
except PackageNotFoundError:  # running from a source tree without installing
    __version__ = "0.0.0+unknown"
