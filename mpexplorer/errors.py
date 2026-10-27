class BackendError(Exception):
    """A filesystem or transport operation failed."""


class Cancelled(Exception):
    """The user cancelled the running job."""
