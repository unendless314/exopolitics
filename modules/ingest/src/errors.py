from enum import Enum
from typing import Optional


class ErrorClass(str, Enum):
    NETWORK = "network_error"
    TIMEOUT = "timeout_error"
    HTTP_4XX = "http_error_4xx"
    HTTP_5XX = "http_error_5xx"
    PARSE = "parse_error"
    UNEXPECTED = "unexpected_error"


EMITTABLE_ERROR_CLASSES = frozenset(error_class.value for error_class in ErrorClass)


class ErrorClassContractError(ValueError):
    """Raised when an error class is outside the application write contract."""


def validate_error_class(
    value: Optional[str],
    *,
    table: str,
    column: str,
) -> None:
    if value is not None and value not in EMITTABLE_ERROR_CLASSES:
        raise ErrorClassContractError(
            f"Invalid error class {value!r} for {table}.{column}"
        )
