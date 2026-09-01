"""User-scoped TMS Local Agent for compute-to-local-data Quick Analysis."""

from .app import create_app
from .config import AgentConfig

__all__ = ["AgentConfig", "create_app"]
__version__ = "0.1.0"
