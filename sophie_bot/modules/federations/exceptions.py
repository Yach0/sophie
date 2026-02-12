from __future__ import annotations


class FederationServiceError(Exception):
    """Base exception for all federation service errors."""


class FederationValidationError(FederationServiceError):
    """Raised when federation validation fails."""


class FederationNotFoundError(FederationServiceError):
    """Raised when a federation cannot be found."""


class FederationContextError(FederationServiceError):
    """Raised when federation context cannot be determined."""


class FederationPermissionError(FederationServiceError):
    """Raised when user lacks required federation permissions."""


class FederationBanValidationError(FederationValidationError):
    """Raised when ban validation fails."""


class FederationAlreadyExistsError(FederationServiceError):
    """Raised when attempting to create a federation that already exists."""


class FederationLimitExceededError(FederationServiceError):
    """Raised when user exceeds federation creation limit."""


class FederationSubscriptionError(FederationServiceError):
    """Raised when federation subscription operations fail."""


class FederationTransferError(FederationServiceError):
    """Raised when federation transfer operations fail."""
