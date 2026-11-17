"""Compatibility launcher; use throwtask.admin for new configurations."""

from throwtask import admin as _implementation


def __getattr__(name):
    return getattr(_implementation, name)


if __name__ == "__main__":
    _implementation.main()
