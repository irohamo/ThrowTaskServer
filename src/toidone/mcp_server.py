"""Compatibility launcher; use throwtask.mcp_server for new configurations."""

from throwtask import mcp_server as _implementation


def __getattr__(name):
    return getattr(_implementation, name)


if __name__ == "__main__":
    _implementation.main()
