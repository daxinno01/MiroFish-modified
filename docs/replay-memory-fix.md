# Replay memory and connection recovery

Replay views no longer load the event history when they are constructed. Project, usage, and replay-status requests can run during ontology playback without opening event payloads.

Each replay owns `events.sqlite3` alongside `session.json`. The index contains JSONL byte offsets, stage, kind, time, sequence, and action filter fields. Queries select visible offsets before decoding and remapping payloads. Latest snapshots use one selected row; action pagination and incremental log offsets are applied in SQL. Simulation database pages are also selected in SQL.

Recording validation, task ID collection during replay creation, and index construction stream the JSONL. Artifact checksums use 1 MiB chunks. Recording version 1 and source recordings are preserved.

Missing or outdated indexes rebuild on demand. Builders use a per-session thread and process lock and publish a temporary SQLite database with an atomic rename. Replay creation and indexing log IDs, duration, event counts, and failure types through `mirofish.replay`; payloads are not logged.

The ontology view and saved simulation indicator share one replay-status poller. Polls start after the preceding request and its consumer callbacks finish. Connection failures retry after 1, 2, 4, 8, then 10 seconds, retaining the replay ID. Navigation aborts requests and cancels scheduled polling. Graph, task, and usage polls also schedule from completion.

Vite keeps `http://127.0.0.1:5001` as its backend target. Connection errors return JSON with HTTP 503 and `code: BACKEND_UNAVAILABLE`. The UI displays “Backend unavailable; reconnecting” until requests recover.

## Automated checks

Run from the repository root using the installed WSL packages:

```sh
backend/.venv/bin/python -m unittest discover -s backend/tests -v
node frontend/tests/replay-polling.mjs
node frontend/tests/ontology-request.mjs
npm run build
```

The 27 backend tests include a 100,795,903-byte synthetic history. Creation uses approximately 2.1 MiB of peak traced Python allocations. After warm-up, RSS is unchanged across four batches of 120 concurrent requests, following 80 warm-up requests. A query tripwire confirms that ontology project, usage, and status requests never select event payloads. Other checks cover atomic index recovery, selective decoding, pagination, milestone visibility, stop/resume, independent sessions, and live-service tripwires.

## Local runtime checks

With ports 3000 and 5001 free, start the verification development stack in WSL:

```sh
backend/.venv/bin/python backend/tests/replay_runtime_check.py start
```

This starts Vite and the application with live generation disabled by tripwires. It checks a real proxy connection failure before starting the backend. Process IDs are saved in `/tmp/mirofish-replay-dev.json`; logs are `/tmp/mirofish-vite.log` and `/tmp/mirofish-backend.log`.

On Windows, with the installed Chromium cache and Node 24, run:

```powershell
node frontend/tests/replay-browser.mjs
```

This checks the actual ontology view and indicator with slow status requests, a simulated 503 outage, recovery without creating a replay, and cancellation on navigation. It temporarily pauses the latest existing replay's ontology clock and restores its original session afterward.

Then run in WSL:

```sh
backend/.venv/bin/python backend/tests/replay_runtime_check.py exercise
```

This exercises every stage of the existing replay with concurrent requests, checks port 5001 and memory, restarts the verification backend, and verifies the persisted replay and index. It restores the original replay clock and leaves the development stack running with live analysis disabled.
