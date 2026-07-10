"""diwa-connectors — shared read-only MCP tool server for Diwa.

One process hosts one tool group per university system. Groups are enabled by
configuration only; a group whose base URL is unset simply does not exist on
the wire. All tools are read-only by design — write tools belong in a
system-owned server with its own kill switch (see the AIS MCP server).
"""

__version__ = "0.1.0"
