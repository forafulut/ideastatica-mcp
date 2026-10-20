"""Translation of SDK / network failures into readable MCP tool errors."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from mcp.server.mcpserver.exceptions import ToolError

logger = logging.getLogger(__name__)

STATUS_HINTS = {
    400: "the request was rejected as invalid; check the ids and values you passed",
    401: "the service does not know this client id (the service was probably restarted); the next call reconnects automatically - re-open the project",
    404: "not found - the project may have been closed, the service restarted, or the id is wrong; call list_open_projects / list_connections",
    409: "conflict - most often a calculation job is already running for this project (see get_calculation_job / cancel_calculation_job)",
    422: "unprocessable - an id or value is not valid for this connection (unknown member/load-effect id, invalid expression, ...)",
    500: "internal error inside the IDEA StatiCa service; the details above usually name the cause",
}


class IdeaError(Exception):
    """A user-facing error raised by the MCP layer itself (bad arguments, missing state)."""


class RestError(IdeaError):
    """An HTTP error from a raw REST call (operation endpoints outside the SDK)."""

    def __init__(self, status: int, reason: str | None, body: str):
        self.status = status
        self.reason = reason
        self.body = body
        details = _problem_details(body, None)
        hint = STATUS_HINTS.get(int(status) if status else 0)
        message = f"HTTP {status} {reason or ''}".strip()
        if details:
            message += f": {details}"
        if hint:
            message += f" ({hint})"
        super().__init__(message)


def _problem_details(body: str | None, data: object) -> str | None:
    payload: object = data
    if payload is None and body:
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            return body.strip() or None
    if isinstance(payload, dict):
        parts = []
        for key in ("title", "detail"):
            value = payload.get(key)
            if value and value not in parts:
                parts.append(str(value))
        errors = payload.get("errors")
        if isinstance(errors, dict):
            for field, messages in errors.items():
                if isinstance(messages, list):
                    parts.append(f"{field}: {'; '.join(str(m) for m in messages)}")
                else:
                    parts.append(f"{field}: {messages}")
        if parts:
            return " | ".join(parts)
        return json.dumps(payload)[:2000]
    if payload is not None:
        return str(payload)[:2000]
    return None


def describe_exception(exc: BaseException) -> str:
    """Human readable description of an SDK/transport exception."""
    try:
        from ideastatica_connection_api.exceptions import ApiException
    except ImportError:  # pragma: no cover
        ApiException = ()  # type: ignore[assignment]

    if isinstance(exc, ApiException):
        status = exc.status
        message = f"HTTP {status} {exc.reason or ''}".strip()
        details = _problem_details(exc.body, exc.data)
        if details:
            message += f": {details}"
        hint = STATUS_HINTS.get(int(status) if status else 0)
        if hint:
            message += f" ({hint})"
        return message

    name = type(exc).__name__
    text = str(exc)
    lowered = text.lower()
    if "connection refused" in lowered or "max retries" in lowered or "newconnectionerror" in lowered or "actively refused" in lowered:
        return (
            f"cannot reach the IDEA StatiCa Connection REST API service ({name}: {text[:300]}). "
            "Use service_status / service_start."
        )
    if isinstance(exc, IdeaError):
        return text
    return f"{name}: {text}" if text else name


@contextmanager
def tool_errors(action: str) -> Iterator[None]:
    """Convert every failure into a ToolError carrying a readable message.

    The MCP SDK already prefixes the message with the tool name, so ``action`` is only used
    for the server-side log.
    """
    try:
        yield
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see errors.py docstring
        logger.debug("%s failed", action, exc_info=True)
        raise ToolError(describe_exception(exc)) from exc
