from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("newsroom")
except PackageNotFoundError:  # not installed
    __version__ = "0.0.0"

_ci_check: int = "not an int"
