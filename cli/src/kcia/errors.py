"""kcia exception hierarchy."""


class KciaError(Exception):
    """Base error for kcia."""


class NotImplementedCommandError(KciaError):
    """Raised when a command is still a stub."""
