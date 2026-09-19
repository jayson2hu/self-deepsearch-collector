"""Versioned real-source parsers.

Connectors in this package parse explicitly supplied source evidence. Network
collection remains controlled by the source registry and is disabled by
default.
"""

from collector.connectors.javdb import CONNECTOR_VERSION as JAVDB_CONNECTOR_VERSION

__all__ = ["JAVDB_CONNECTOR_VERSION"]
