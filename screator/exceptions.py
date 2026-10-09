class AppError(Exception):
    """User-facing pipeline error."""


class InvalidMediaError(AppError):
    """Media is truncated, corrupt, or missing a required stream."""
