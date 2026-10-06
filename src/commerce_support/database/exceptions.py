class DatabaseConfigurationError(RuntimeError):
    """Raised when the database cannot be configured without exposing its URL."""


class ConversationNotFoundError(LookupError):
    """Raised when a client refers to a conversation that does not exist."""


class TurnConflictError(RuntimeError):
    """Raised when a conversation already has a live turn in progress."""


class TurnOwnershipError(RuntimeError):
    """Raised when a turn no longer owns its conversation lease."""


class ToolCallNotFoundError(RuntimeError):
    """Raised when a tool result has no matching assistant tool call."""


class ToolResultConflictError(RuntimeError):
    """Raised when a repeated tool call is given a different stored result."""


class CorruptHistoryError(RuntimeError):
    """Raised when stored successful messages do not form valid tool-call pairs."""
