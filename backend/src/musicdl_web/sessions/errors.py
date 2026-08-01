"""Sanitized session boundary errors."""


class SessionError(Exception):
    """Base error that never includes platform credentials."""


class SessionImportError(SessionError):
    """A manually supplied session header was invalid."""


class SessionPersistenceError(SessionError):
    """Encrypted session state could not be safely read or written."""


class QrLoginError(SessionError):
    """An experimental QR transition was invalid."""


class QrLoginUnavailable(QrLoginError):
    """Experimental QR login is disabled or unavailable."""
