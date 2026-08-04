"""Credential redaction.

The vendor SDK builds log lines and exception messages from raw HTTP request
bodies and headers. For the OAuth endpoints those contain the account's access
and refresh tokens, so anything forwarding the SDK's text unfiltered leaks
credentials into add-on logs that users routinely paste into bug reports.
"""

from __future__ import annotations

import logging

from bridge.redact import RedactingFilter, redact, safe_error

#: Deliberately not shaped like a real JWT: a fixture that looks like a
#: genuine credential trips secret scanners and invites a reader to wonder
#: whether it ever was one.
_TOKEN = "NOT-A-REAL-TOKEN-0123456789abcdef"


class TestRedact:
    def test_scrubs_json_style_access_token(self) -> None:
        result = redact(f'{{"access_token": "{_TOKEN}", "expires_ts": 1}}')
        assert _TOKEN not in result
        assert "expires_ts" in result

    def test_scrubs_form_style_refresh_token(self) -> None:
        result = redact(f"client_id=123&refresh_token={_TOKEN}&x=1")
        assert _TOKEN not in result

    def test_scrubs_bearer_header(self) -> None:
        result = redact(f"Authorization: Bearer {_TOKEN}")
        assert _TOKEN not in result

    def test_scrubs_authorization_code(self) -> None:
        """The OAuth code is single-use but still grants a session if replayed."""
        result = redact(f"code={_TOKEN}&state=abc")
        assert _TOKEN not in result

    def test_is_case_insensitive(self) -> None:
        assert _TOKEN not in redact(f'"Access_Token": "{_TOKEN}"')

    def test_keeps_surrounding_text_useful(self) -> None:
        """Redaction must not destroy the diagnostic value of the message."""
        result = redact(f'invalid http response, 401, {{"refresh_token": "{_TOKEN}"}}')
        assert "invalid http response" in result
        assert "401" in result

    def test_leaves_ordinary_text_untouched(self) -> None:
        message = "camera 12345 did not connect within 30s"
        assert redact(message) == message


class TestSafeError:
    """A bare ``token=`` must be caught too -- the SDK is inconsistent."""

    def test_keeps_the_exception_type(self) -> None:
        err = ValueError(f"token={_TOKEN}")
        result = safe_error(err)
        assert result.startswith("ValueError")
        assert _TOKEN not in result


class TestRedactingFilter:
    def test_scrubs_the_message(self) -> None:
        record = logging.LogRecord(
            "x", logging.WARNING, "f", 1, f"access_token={_TOKEN}", None, None
        )
        RedactingFilter().filter(record)
        assert _TOKEN not in record.getMessage()

    def test_scrubs_interpolated_arguments(self) -> None:
        """The SDK's text usually arrives as an argument, not the format string."""
        record = logging.LogRecord(
            "x", logging.ERROR, "f", 1, "refresh failed: %s", (f"code={_TOKEN}",), None
        )
        RedactingFilter().filter(record)
        assert _TOKEN not in record.getMessage()

    def test_scrubs_dict_arguments(self) -> None:
        # logging unwraps a single mapping argument into ``record.args``; the
        # tuple is how a caller actually passes one.
        record = logging.LogRecord(
            "x",
            logging.ERROR,
            "f",
            1,
            "%(detail)s",
            ({"detail": f"code={_TOKEN}"},),
            None,
        )
        RedactingFilter().filter(record)
        assert _TOKEN not in record.getMessage()

    def test_passes_the_record_through(self) -> None:
        record = logging.LogRecord("x", logging.INFO, "f", 1, "hello", None, None)
        assert RedactingFilter().filter(record) is True


class TestNonStringArguments:
    """The SDK logs whole device dictionaries as a single argument.

    Scrubbing the message and its string arguments separately left those
    untouched, so a device token reached the add-on log -- the same log the
    troubleshooting instructions tell users to raise to debug and attach to a
    bug report.
    """

    def formatted(self, message: str, *args: object) -> str:
        record = logging.LogRecord(
            "miot.cloud", logging.INFO, __file__, 1, message, args, None
        )
        RedactingFilter().filter(record)
        return record.getMessage()

    def test_a_token_inside_a_dict_argument_is_scrubbed(self) -> None:
        device = {"did": "blt.3.abc", "token": "74582bb13215ef2a1c89659d"}
        result = self.formatted("missing the urn field, cloud, %s", device)

        assert "74582bb13215ef2a1c89659d" not in result
        # The rest of the record still has to be readable, or the log stops
        # being useful for the thing it was written for.
        assert "blt.3.abc" in result

    def test_a_token_in_a_nested_structure_is_scrubbed(self) -> None:
        result = self.formatted("%s", [{"extra": {"access_token": "abcd1234efgh"}}])
        assert "abcd1234efgh" not in result

    def test_ordinary_records_are_unchanged(self) -> None:
        assert self.formatted("started %s in %d ms", "go2rtc", 12) == (
            "started go2rtc in 12 ms"
        )

    def test_a_broken_format_string_does_not_lose_the_record(self) -> None:
        # Passing it through unchanged keeps the failure where it belongs.
        record = logging.LogRecord(
            "x", logging.INFO, __file__, 1, "%d", ("not a number",), None
        )
        assert RedactingFilter().filter(record) is True


class TestUrlCredentials:
    """ffmpeg quotes the URL it was given in almost every diagnostic.

    The bridge builds `rtsp://user:password@127.0.0.1:8554/...` for ffmpeg to
    read its own published stream, and those diagnostics reach both the log and
    the add-on page, where a failed preview shows the reason it was given. The
    password must not survive that trip.
    """

    def test_a_password_in_an_rtsp_url_is_removed(self) -> None:
        line = "rtsp://xiaomi:probe-1234@127.0.0.1:8554/camera_42: Connection refused"
        result = redact(line)

        assert "probe-1234" not in result
        # The username stays: it is what makes the line diagnosable, and it is
        # not the secret.
        assert "xiaomi" in result
        assert "Connection refused" in result

    def test_it_works_anywhere_in_the_line(self) -> None:
        result = redact("[rtsp @ 0x55] Could not open rtsp://u:s3cr3t@host/x")
        assert "s3cr3t" not in result

    def test_a_url_without_credentials_is_untouched(self) -> None:
        line = "http://127.0.0.1:8099/api/stream/42"
        assert redact(line) == line

    def test_a_plain_url_with_a_port_is_untouched(self) -> None:
        # `host:8554` looks like `user:password` to a careless pattern.
        line = "rtsp://127.0.0.1:8554/camera_42: Server returned 401"
        assert redact(line) == line
