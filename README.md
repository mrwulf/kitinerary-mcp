# kitinerary-mcp

An [MCP](https://modelcontextprotocol.io) server that wraps KDE's [`kitinerary-extractor`](https://invent.kde.org/pim/kitinerary) CLI: give it a travel confirmation email, PDF ticket, or Apple/Google Wallet `.pkpass` file, and it returns whatever structured `schema.org` `Reservation` JSON-LD the file contains — deterministically, with no LLM in the loop.

Most real airline/hotel confirmation emails already embed this JSON-LD (it's the same markup Gmail parses for its own "smart" trip cards). `kitinerary-extractor` reads it straight out, along with structured PDF tickets and Wallet passes that plain text/regex extraction can't touch. This server is a thin, stateless shim around that binary so any MCP client can call it.

## What this is (and isn't)

- **Is**: a single tool, `extract_booking`, that takes a file and returns raw JSON-LD (or nothing, if the extractor finds nothing). No outbound network calls, no secrets, no state — every call is an isolated subprocess against a temp file, cleaned up immediately after.
- **Isn't**: a booking-type mapper. It does not translate the JSON-LD into any particular app's reservation schema — that's a deliberate boundary, so this stays reusable across whatever consumer calls it rather than coupled to one caller's data model.

## How it's used

This server powers the deterministic extraction stage of a self-hosted [TripIt](https://www.tripit.com/) replacement: forward a booking confirmation email, an n8n workflow calls `extract_booking` on it, and a matched result gets mapped and written into [Trek](https://github.com/liketrek/trek) as a trip — no LLM involved for the common case, with an LLM fallback only for emails this server finds nothing in. See [`tripit_replacement.md`](https://github.com/mrwulf/home-cluster/blob/main/docs/tripit_replacement.md) in that pipeline's repo for the full architecture, the JSON-LD-to-booking mapping table this server's output feeds into, and the gotchas found integrating it (some datetime fields come back as `{"@type":"QDateTime",...}` objects rather than plain strings, multi-leg/multi-passenger handling, and more).

That pipeline is one consumer, not a dependency this server has on it — nothing in this repo is coupled to it, and any MCP client can use `extract_booking` the same way. If you build something else on top of this server, opening a PR to list it here is welcome.

## Tool interface

```
extract_booking(file_base64: str, filename: str, context_date: str | None = None) -> {
  items: object[],    # the raw JSON-LD array kitinerary-extractor emitted (possibly empty)
  warnings: string[], # e.g. "unsupported file type", "no reservation data found in file"
}
```

- `file_base64` — the file's raw bytes, base64-encoded (MCP tool args are JSON, so there's no binary/multipart transport at this layer).
- `filename` — used to infer format from the extension. Accepted: `.eml`, `.pdf`, `.pkpass`, `.html`, `.txt`. Anything else is rejected with a warning, not silently passed through.
- `context_date` — optional ISO date/time, forwarded to `kitinerary-extractor --context-date`. Helps resolve dates that don't state a year or are relative to "today" (pass the email's own `Date:` header if you have it).
- No match is **not** an error — you get back `items: []` with a warning. Tool errors are reserved for genuine failures: the binary is missing, the process crashed, or the file exceeds the size cap.
- Limits: 10 MB decoded file size, 60s extraction timeout. Both fail cleanly (a warning, or a tool error) rather than hanging or crashing the caller.

## Running it

```sh
docker run --rm -i ghcr.io/mrwulf/kitinerary-mcp:v0.1.0
```

It speaks MCP over stdio. Wire it into any MCP client's stdio server config, e.g. Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kitinerary": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "ghcr.io/mrwulf/kitinerary-mcp:v0.1.0"]
    }
  }
}
```

Or point a [ToolHive](https://github.com/stacklok/toolhive) `MCPServer` at the same image with `transport: stdio` — no secrets, no volumes needed.

Always pin an exact tag; `latest` is published alongside each release for convenience but isn't meant to be what you deploy against.

### Tags and version strings

Every release publishes three tags:

- `vX.Y.Z` — this server's own version, e.g. `v0.1.0`. Pin this in normal use.
- `vX.Y.Z-kitineraryA.B.C` — the same build, with the bundled `kitinerary-extractor` release folded into the tag (e.g. `v0.1.0-kitinerary24.12.3`), so you can tell which upstream KDE release a given build carries without pulling it or reading the Dockerfile. Pin this instead if your own compatibility depends on a specific `kitinerary` behavior.
- `latest` — convenience only, not for pinning.

The same two version numbers are also on the image itself, so you don't have to trust the tag alone: as OCI labels (`org.opencontainers.image.version`, `io.github.mrwulf.kitinerary-mcp.kitinerary-version`) readable via `docker inspect`, and in the running server's own MCP `version` field (`0.1.0+kitinerary.24.12.3`, semver build-metadata form) that any MCP client can read after connecting.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest

# Fast unit tests (mock the CLI subprocess, no image build needed)
.venv/bin/pytest tests/test_server.py -v

# Real end-to-end tests against the actual kitinerary-extractor binary
docker build -t kitinerary-mcp:test .
IMAGE_TAG=kitinerary-mcp:test .venv/bin/pytest tests/test_integration.py -v
```

## Dependency footprint

`libkitinerary-bin` pulls in a genuine KDE Frameworks 6 + Qt 6 chain (yes, including `libqt6gui6`/`libqt6qml6`, even for this headless CLI use) — there's no slimmer package upstream, so expect a multi-hundred-MB image. This is a tradeoff of using the reference KDE implementation rather than reimplementing extraction logic from scratch, which would mean re-deriving and maintaining parsers for every airline/hotel/rail JSON-LD dialect ourselves.

## License

This repository's own code (`server.py` and supporting files) is [MIT-licensed](LICENSE). The built image additionally includes KDE's `kitinerary` library, which is `LGPL-2.0-or-later` (confirmed from `/usr/share/doc/libkitinerary-bin/copyright` in the Debian trixie package, version `24.12.3-1`). This server only invokes `kitinerary-extractor` as a subprocess — it does not link against `libkitinerary` — so using this image imposes no LGPL obligations on your own application code; the obligations that do apply (source availability for the library itself, etc.) are already satisfied by Debian's own package distribution.

## Keeping this current as kitinerary grows

Two independently-versioned things need tracking, and Renovate handles them differently:

1. **This image's own published tag** — a normal container reference; any downstream consumer's tooling (Renovate, Flux, etc.) tracks it exactly like any other pinned image.
2. **The `libkitinerary-bin`/`libkitinerary-data` apt package versions pinned in the `Dockerfile`** — Renovate has no native Debian-apt datasource, so these are pinned exactly (never a bare `apt-get install` with no version) and annotated for a `customManagers` regex against `KDE/kitinerary`'s GitHub releases as a freshness signal:

   ```dockerfile
   # renovate: depName=KDE/kitinerary datasource=github-releases
   ARG KITINERARY_VERSION=24.12.3
   ```

   Debian's version string tracks upstream KDE Gear releases closely but appends its own revision suffix (`-1`, `-2`, ...) that a Renovate bump can't verify still resolves in the `trixie` archive on build day. If it doesn't, the CI build simply fails loudly — an accepted, visible failure mode rather than trying to fully automate around Debian's package cadence.
3. **The Python `mcp` SDK version** — pinned in `requirements.txt`; any pip-aware dependency bot (Renovate, Dependabot) tracks this natively.

## Acceptance test

The fixtures in `tests/fixtures/` and `tests/test_integration.py` cover the smoke test this server is expected to pass before any release: a synthetic hotel confirmation with embedded `LodgingReservation` JSON-LD extracts correctly, a plain marketing email with no structured data returns an empty result with a warning (not an error), and an oversized file is rejected cleanly.
