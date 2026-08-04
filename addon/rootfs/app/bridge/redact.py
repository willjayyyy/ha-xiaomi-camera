"""Redaction of secrets in log output and error messages.

The vendor SDK builds exception messages and log lines from raw HTTP request
bodies and headers, which for the OAuth endpoints contain the account's access
and refresh tokens. Anything that forwards ``str(err)`` from the SDK -- to a
log, to an HTTP response, to the UI -- therefore leaks credentials unless it is
scrubbed first.

Add-on logs are visible in the Supervisor UI and routinely pasted into bug
reports, and a leaked refresh token is durable access to the account's cameras.
Redaction is applied at the logging root so it covers the SDK's own loggers as
well as this package's.
"""

from __future__ import annotations

import logging
import re
from typing import Final

_PLACEHOLDER: Final = "[redacted]"

#: Matches the shapes credentials arrive in: JSON fields, form-encoded pairs,
#: keyword arguments in repr output, bearer headers, and URL userinfo.
_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # Matches a bare `token` as well as the prefixed forms: the SDK is not
    # consistent about which it uses, and a pattern that only covered
    # `access_token` would leave the plain one in the log.
    re.compile(
        r"""(?P<key>["']?(?:[a-z_-]*token|code|password|secret)["']?\s*[:=]\s*["']?)"""
        r"(?P<value>[A-Za-z0-9._~+/=-]{8,})",
        re.IGNORECASE,
    ),
    re.compile(r"(?P<key>Bearer\s+)(?P<value>[A-Za-z0-9._~+/=-]{8,})", re.IGNORECASE),
    # Credentials carried in a URL, as `scheme://user:password@host`. The
    # bridge builds one of these for ffmpeg to read its own RTSP stream, and
    # ffmpeg quotes the URL it was given in every diagnostic it emits -- which
    # then reaches the log and the add-on page. The password is replaced and
    # the username kept, because the username is what makes the line useful.
    re.compile(
        r"(?P<key>[a-z][a-z0-9+.-]*://[^\s:/@]+:)(?P<value>[^\s@/]+)(?=@)",
        re.IGNORECASE,
    ),
)


def redact(text: str) -> str:
    """Replace anything that looks like a credential with a placeholder."""
    result = text
    for pattern in _PATTERNS:
        result = pattern.sub(lambda m: f"{m.group('key')}{_PLACEHOLDER}", result)
    return result


def safe_error(err: BaseException) -> str:
    """A log-safe description of a third-party exception.

    Keeps the exception type, which is the part that aids diagnosis, and
    scrubs the message, which is the part that may embed a token.
    """
    return f"{type(err).__name__}: {redact(str(err))}"


class RedactingFilter(logging.Filter):
    """Scrubs credentials from every record passing through a logger.

    The record is formatted here and the result replaces the message, rather
    than the message and its arguments being scrubbed separately. That is the
    only way to catch an argument that is not a string: the SDK logs whole
    device dictionaries -- ``{'did': ..., 'token': ...}`` -- as a single
    argument, and scrubbing arguments by type left those untouched, putting
    device tokens into a log that users are told to attach to bug reports.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            # A broken format string is the logger's problem, not this
            # filter's; passing it through unchanged keeps the failure where
            # it belongs rather than losing the record entirely.
            return True
        record.msg = redact(message)
        record.args = ()
        return True


def install(root: logging.Logger | None = None) -> None:
    """Attach the filter to every handler on the root logger.

    Filters on a logger do not apply to records propagated from child loggers,
    so the filter is attached to the handlers instead -- that is the one place
    every record passes through regardless of which logger produced it.
    """
    target = root or logging.getLogger()
    redacting = RedactingFilter()
    for handler in target.handlers:
        handler.addFilter(redacting)
