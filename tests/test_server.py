"""Unit tests for the extraction logic. These mock subprocess.run so they
run without the real kitinerary-extractor binary or a container - see
tests/test_integration.py for the real-binary round trip."""

from __future__ import annotations

import base64
import json
import subprocess
from unittest.mock import patch

import pytest

import server


def _run(mock_stdout: bytes, mock_returncode: int = 0, mock_stderr: bytes = b""):
    return subprocess.CompletedProcess(
        args=["kitinerary-extractor"],
        returncode=mock_returncode,
        stdout=mock_stdout,
        stderr=mock_stderr,
    )


def test_rejects_unsupported_extension():
    result = server._extract(base64.b64encode(b"data").decode(), "archive.zip")
    assert result["items"] == []
    assert "unsupported file type" in result["warnings"][0]


def test_rejects_invalid_base64():
    with pytest.raises(ValueError, match="not valid base64"):
        server._extract("not-valid-base64!!!", "confirmation.eml")


def test_rejects_oversized_file():
    oversized = base64.b64encode(b"x" * (server.MAX_FILE_BYTES + 1)).decode()
    result = server._extract(oversized, "confirmation.txt")
    assert result["items"] == []
    assert "too large" in result["warnings"][0]


def test_empty_extraction_is_not_an_error():
    with patch("server.subprocess.run", return_value=_run(b"[]")):
        result = server._extract(base64.b64encode(b"junk").decode(), "note.txt")
    assert result["items"] == []
    assert "no reservation data found" in result["warnings"][0]


def test_successful_extraction_returns_items_verbatim():
    payload = [{"@type": "LodgingReservation", "reservationNumber": "TEST-0001"}]
    with patch(
        "server.subprocess.run", return_value=_run(json.dumps(payload).encode())
    ):
        result = server._extract(base64.b64encode(b"junk").decode(), "note.html")
    assert result["items"] == payload
    assert result["warnings"] == []


def test_timeout_returns_warning_not_error():
    with patch(
        "server.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="kitinerary-extractor", timeout=60),
    ):
        result = server._extract(base64.b64encode(b"junk").decode(), "note.pdf")
    assert result["items"] == []
    assert "timed out" in result["warnings"][0]


def test_missing_binary_raises_runtime_error():
    with patch("server.subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(RuntimeError, match="binary not found"):
            server._extract(base64.b64encode(b"junk").decode(), "note.eml")


def test_nonzero_exit_raises_runtime_error():
    with patch(
        "server.subprocess.run", return_value=_run(b"", 1, b"boom: parse failure")
    ):
        with pytest.raises(RuntimeError, match="exited 1"):
            server._extract(base64.b64encode(b"junk").decode(), "note.eml")


def test_only_stdout_is_parsed_as_json_never_stderr():
    payload = [{"@type": "FlightReservation"}]
    noisy_stderr = b'Detected locale "C"... [{"not": "json-ld"}]'
    with patch(
        "server.subprocess.run",
        return_value=_run(json.dumps(payload).encode(), 0, noisy_stderr),
    ):
        result = server._extract(base64.b64encode(b"junk").decode(), "note.eml")
    assert result["items"] == payload


def test_context_date_is_passed_through_to_cli():
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _run(b"[]")

    with patch("server.subprocess.run", side_effect=fake_run):
        server._extract(base64.b64encode(b"junk").decode(), "note.eml", "2026-01-01")

    assert "--context-date" in captured["cmd"]
    assert "2026-01-01" in captured["cmd"]


def test_single_object_result_is_wrapped_in_a_list():
    payload = {"@type": "LodgingReservation"}
    with patch(
        "server.subprocess.run", return_value=_run(json.dumps(payload).encode())
    ):
        result = server._extract(base64.b64encode(b"junk").decode(), "note.eml")
    assert result["items"] == [payload]
