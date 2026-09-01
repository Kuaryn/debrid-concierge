# debrid-concierge

Small Windows tray app that sends magnet links and .torrent files to a debrid
service (TorBox) and pulls the finished files down over plain HTTPS through
AB Download Manager. The machine never joins the BitTorrent network: no
torrent client, no peer traffic, nothing for a campus or office network to
complain about.

Status: works on my machine. One Windows install, my own TorBox and ABDM
setup, no packaging yet (needs a Python install to run). Treat it as a
working prototype, not a product.

## What happens on a click

1. Click a magnet link or a .torrent file in the browser.
2. Windows starts `win_handler.py`, which spawns a detached worker and
   returns at once, so the browser never waits.
3. The worker asks for a download folder (tkinter dialog, remembers the
   last choice), adds the torrent to TorBox, and polls until the cloud
   download finishes.
4. Finished files are handed to ABDM's local API, which downloads them
   over HTTPS.
5. The tray shows a toast when the job finishes or fails.

State lives in `%APPDATA%\debrid-concierge`: `jobs.json` (job records)
and `config.json` (TorBox key, last folder choice).

## Design decisions and known limits

**One worker at a time.** Every click spawns a fresh worker process, so two
workers could race on `jobs.json`. Workers take a named mutex; the one that
holds it drains every unfinished job before releasing. A second click while
a job runs just waits its turn. Coarse on purpose: for a single-user desktop
tool, correctness first, and parallel workers would buy nothing.

**Ambiguous cloud adds.** If the request that adds a torrent times out on my
side but succeeded on TorBox's, submitting again would add the same torrent
twice. On that failure the worker lists the cloud torrents and adopts the
one with a matching infohash instead of resubmitting.

**The ABDM handoff is not idempotent.** ABDM's local API has no idempotency
key and returns no job id, so a crash after it accepts a file but before
`jobs.json` records it replays that file on the next run. The per-file
record shrinks the window to one file, but only ABDM could close it. Worst
case is a duplicate download, not a lost one.

**Tokens.** The TorBox key is DPAPI-encrypted before it touches disk and
never appears on a command line. TorBox download URLs embed the key as a
query parameter, so error text gets redacted before it reaches the job log.

## Repo layout

- `src/concierge/orchestrator.py` — job state machine
  (received → cloud_pending → ready → done | failed), persistence, infohash
  reconciliation
- `src/concierge/providers/torbox.py` — TorBox API client
- `src/concierge/abdm.py` — ABDM local API client
- `src/concierge/worker.py` — mutex, folder dialog, poll and handoff loop
- `src/concierge/handlers.py` — CLI that the shell integration calls
- `src/concierge/tray.py`, `dialog.py`, `lock.py`, `config.py`
- `win_handler.py`, `win_worker.py`, `win_tray.py` — registry entry points
- `repoint.py` — writes the HKCU registry keys: magnet handler, .torrent
  ProgID, tray autostart

## Setup

Python 3.12+, then `pip install requests pystray`. Store the TorBox key
(prompted, never echoed):

    python src/concierge/config.py set-key

Point Windows at the app:

    python repoint.py

Then pick "Debrid Concierge" once in Open With for .torrent files; Windows
will not let a script set that default for you.

## Tests

    python -m ruff check src tests repoint.py win_handler.py win_tray.py win_worker.py
    python -m pytest -q -m "not integration"

The integration marker adds four read-only smoke tests against the real
TorBox API using your stored key:

    python -m pytest -m integration -v

License: MIT
