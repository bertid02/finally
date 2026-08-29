"""Pytest configuration and fixtures.

asyncio_mode = "auto" in pyproject.toml handles async test collection; no
event-loop fixture is needed (and overriding the policy raises a
DeprecationWarning on Python 3.14+).
"""
