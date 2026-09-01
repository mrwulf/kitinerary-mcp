"""MCP server wrapping the kitinerary-extractor CLI.

One tool: extract_booking. Takes a file, returns whatever structured
schema.org Reservation JSON-LD kitinerary-extractor can pull out of it.
No booking-type mapping, no outbound network calls, no state - see README.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import subprocess
import tempfile

from mcp.server.mcpserver import MCPServer

KITINERARY_EXTRACTOR_BIN = os.environ.get(
    "KITINERARY_EXTRACTOR_BIN",
    "/usr/lib/x86_64-linux-gnu/libexec/kf6/kitinerary-extractor",
)
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB, matches Trek's own booking-import limit
EXTRACTION_TIMEOUT_SECONDS = 60
ACCEPTED_EXTENSIONS = {".eml", ".pdf", ".pkpass", ".html", ".txt"}

# Two independently-versioned things (see README "Keeping this current"):
# this server's own version, and the kitinerary-extractor build it wraps.
# Both are baked into the image as env vars at build time (Dockerfile ARGs).
SERVER_VERSION = os.environ.get("SERVER_VERSION", "0.0.0-dev")
KITINERARY_VERSION = os.environ.get("KITINERARY_VERSION", "unknown")

mcp = MCPServer(
    "kitinerary-extractor",
    version=f"{SERVER_VERSION}+kitinerary.{KITINERARY_VERSION}",
)


def _extract(file_base64: str, filename: str, context_date: str | None = None) -> dict:
    """Core extraction logic, kept separate from the tool decorator so it's
    directly unit-testable without going through the MCP transport."""
    warnings: list[str] = []

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ACCEPTED_EXTENSIONS:
        return {
            "items": [],
            "warnings": [
                f"unsupported file type {ext!r}; accepted: "
                + ", ".join(sorted(ACCEPTED_EXTENSIONS))
            ],
        }

    try:
        raw = base64.b64decode(file_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"file_base64 is not valid base64: {exc}") from exc

    if len(raw) > MAX_FILE_BYTES:
        return {
            "items": [],
            "warnings": [
                f"file too large: {len(raw)} bytes exceeds the "
                f"{MAX_FILE_BYTES} byte limit"
            ],
        }

    fd, path = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)

        cmd = [KITINERARY_EXTRACTOR_BIN, "-o", "JsonLd"]
        if context_date:
            cmd += ["--context-date", context_date]
        cmd.append(path)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=EXTRACTION_TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"kitinerary-extractor binary not found at {KITINERARY_EXTRACTOR_BIN}"
            ) from exc
        except subprocess.TimeoutExpired:
            return {
                "items": [],
                "warnings": [
                    f"extractor timed out after {EXTRACTION_TIMEOUT_SECONDS}s"
                ],
            }

        if result.returncode != 0:
            raise RuntimeError(
                f"kitinerary-extractor exited {result.returncode}: "
                f"{result.stderr.decode('utf-8', 'replace')[:2000]}"
            )

        stdout = result.stdout.decode("utf-8", "replace").strip()
        if not stdout:
            return {"items": [], "warnings": ["extractor produced no output"]}

        try:
            items = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"kitinerary-extractor produced non-JSON stdout: {exc}"
            ) from exc

        if not isinstance(items, list):
            items = [items]

        if not items:
            warnings.append("no reservation data found in file")

        return {"items": items, "warnings": warnings}
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


@mcp.tool()
def extract_booking(
    file_base64: str, filename: str, context_date: str | None = None
) -> dict:
    """Extract structured schema.org Reservation JSON-LD from a travel
    confirmation file (email, PDF ticket, or Wallet pass).

    Args:
        file_base64: The file's raw bytes, base64-encoded.
        filename: Original filename, used to infer format from its
            extension. Accepted: .eml, .pdf, .pkpass, .html, .txt.
        context_date: Optional ISO date/time (e.g. the email's own Date:
            header) to help the extractor resolve dates that don't state
            a year or are relative to "today".

    Returns:
        items: the raw JSON-LD array kitinerary-extractor emitted
            (possibly empty - no match is not an error).
        warnings: human-readable notes, e.g. "extractor timed out",
            "unsupported file type", "no reservation data found in file".
    """
    return _extract(file_base64, filename, context_date)


if __name__ == "__main__":
    mcp.run(transport="stdio")
