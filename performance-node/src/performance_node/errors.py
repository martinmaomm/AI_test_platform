"""Errors whose text is deliberately safe to present in client logs."""


class NodeError(RuntimeError):
    """Base error for an expected client failure."""


class ConfigurationError(NodeError):
    pass


class StateError(NodeError):
    pass


class ProtocolError(NodeError):
    pass


class RetryExhausted(NodeError):
    pass


class AgentStopped(NodeError):
    """A fail-closed condition that must end the run loop."""
