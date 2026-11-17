"""Compatibility launcher; use throwtask.cli for new configurations."""

from throwtask import cli as _implementation


def __getattr__(name):
    return getattr(_implementation, name)


if __name__ == "__main__":
    _implementation.main()
