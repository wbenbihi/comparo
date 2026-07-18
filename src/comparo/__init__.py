"""comparo — HTTP regression & diff testing across environments.

The public surface is the :mod:`comparo.core` engine; the ``cli`` and ``tui``
packages are thin front-ends over it and must never be imported by the core.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version

__all__ = ["__version__"]

try:
    __version__ = version("comparo")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0"
