"""Integration tests that exercise the real kitinerary-extractor binary
inside the built container image - the unit tests in test_server.py mock
subprocess.run entirely, so this is what actually proves the CLI + image
combination works.

Requires the image to be built first, e.g.:
    docker build -t kitinerary-mcp:test .
    IMAGE_TAG=kitinerary-mcp:test pytest tests/test_integration.py

Skips automatically if docker isn't available or the image isn't built.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

IMAGE_TAG = os.environ.get("IMAGE_TAG", "kitinerary-mcp:test")
FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker not available"
)


def _extract_in_container(file_bytes: bytes, filename: str, context_date: str = ""):
    """Run server._extract() for real, inside the built image, against the
    real kitinerary-extractor binary. The base64 payload is piped over
    stdin (not a -c argument or a volume mount) so this works regardless
    of host filesystem/SELinux setup and isn't bounded by ARG_MAX for
    larger fixtures.
    """
    b64 = base64.b64encode(file_bytes).decode()
    code = (
        "import sys; sys.path.insert(0, '/app'); "
        "import server, json; "
        "b64 = sys.stdin.read(); "
        f"r = server._extract(b64, {filename!r}, {context_date!r} or None); "
        "print(json.dumps(r))"
    )
    result = subprocess.run(
        ["docker", "run", "--rm", "-i", "--entrypoint", "python3", IMAGE_TAG, "-c", code],
        input=b64.encode(),
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"container exited {result.returncode}: "
        f"{result.stderr.decode('utf-8', 'replace')}"
    )
    return json.loads(result.stdout.decode())


@pytest.fixture(scope="module", autouse=True)
def _require_image():
    check = subprocess.run(
        ["docker", "image", "inspect", IMAGE_TAG], capture_output=True
    )
    if check.returncode != 0:
        pytest.skip(f"{IMAGE_TAG} not built - run: docker build -t {IMAGE_TAG} .")


def test_lodging_reservation_is_extracted():
    fixture = (FIXTURES / "lodging_reservation.html").read_bytes()
    result = _extract_in_container(fixture, "confirmation.html")
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["@type"] == "LodgingReservation"
    assert item["reservationNumber"] == "TEST-0001"
    assert item["reservationFor"]["name"] == "Test Hotel Sandbox"


def test_marketing_email_yields_no_items_and_no_error():
    fixture = (FIXTURES / "marketing_email.html").read_bytes()
    result = _extract_in_container(fixture, "marketing.html")
    assert result["items"] == []
    assert len(result["warnings"]) >= 1


def test_oversized_file_is_rejected_cleanly():
    oversized = b"x" * (10 * 1024 * 1024 + 1)
    result = _extract_in_container(oversized, "huge.txt")
    assert result["items"] == []
    assert "too large" in result["warnings"][0]
