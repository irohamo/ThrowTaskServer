"""Compatibility launcher; use throwtask.server for new configurations."""

from throwtask import server as _implementation


def __getattr__(name):
    return getattr(_implementation, name)


if __name__ == "__main__":
    _implementation.main()
