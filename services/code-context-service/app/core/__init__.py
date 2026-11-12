"""Minimal core package exports avoiding circular FastAPI dependency imports."""

from app.core.config import Settings, load_settings

__all__ = ["Settings", "load_settings"]
