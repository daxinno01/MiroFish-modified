# Saved simulations

Select **Record for saved simulation** on the home page before starting a live
analysis. Recording is off by default and applies only to new runs. Finish the
simulation and report, then open **Saved simulations** to replay the recording.
Incomplete recordings appear in the picker with their missing stages. Capture
errors appear there too; they do not fail the live analysis.

The workflow uses its existing graph, preparation, simulation and report screens.
Ontology starts when the replay session is created. Start each later stage with
its normal workflow action. **Saved simulation** remains visible throughout,
with Stop, Resume and Start over controls. Start over allocates another session.
Outcome-changing controls are unavailable. Live interviews, report chat and
explicit translations become available after report playback completes.

Set `SAVED_SIMULATION_STAGE_SECONDS=10` in `.env` to control playback duration
for each stage. Use `0` for immediate completion. The value must be finite and
nonnegative; each session retains the value it had when it was created. Stop
persists the elapsed time. Refreshing or restarting the backend retains that
clock; elapsed time continues while an unpaused backend is offline.

## Storage and APIs

Version 1 recordings stay under `backend/uploads/recordings/rec_*`. Structured
artifacts use JSON, timestamped history uses JSONL, and simulation databases use
SQLite backups that include committed WAL data. The completion manifest includes
checksums. A bundle freezes after successful simulation and report completion.
Existing translations at that point are included. A later live translation is
stored in the live run or replay session that requested it.

Draft project checkpoints precede the simulation. Each new simulation creates a
bundle associated with one simulation/report pair and copies the inputs that
produced it. Original entity/node references and agent IDs are retained.

Replay sessions and their artifact copies live under `backend/uploads/replays`.
Project, simulation and report copies also use the normal storage locations with
`rproj_`, `rsim_` and `rreport_` IDs, so the existing interaction services can read
them. Every replay allocates fresh graph and task IDs too. Live usage starts at
zero for each replay. The source bundle is never modified by playback or chat.

| API | Purpose |
| --- | --- |
| `GET /api/recordings` | Recording readiness, missing stages and capture errors |
| `POST /api/replays` with `{"recording_id":"rec_…"}` | Validate a bundle, allocate IDs, begin ontology |
| `GET /api/replays/<replay_id>` | IDs, source mapping, stages and playback positions |
| `POST /api/replays/<replay_id>/stop` | Persist a frozen playback position |
| `POST /api/replays/<replay_id>/resume` | Continue the current stage |

The normal stage and polling APIs dispatch replay IDs before entering their live
handlers. Responses retain their existing shapes and add `run_mode: "saved"`
and `replay_id`. Unknown/missing replay artifacts fail explicitly and cannot
fall through to live analysis. Pagination and incremental log cursors operate
on the artifacts visible at the current stage position.

After report completion, interviews start `run_parallel_simulation.py
--interaction-only`. That path loads the copied profiles and databases, restores
registered users, and enters the existing IPC interview loop. It does not seed
posts or execute simulation rounds. Report chat uses the saved graph and source
text for retrieval, with the existing live LLM for new answers. New interaction
responses are saved to session history and their token usage to the copied run.

No older-run reconstruction, import or export is provided in version 1.

## Verification

Run the offline suite with the project's Python environment:

```sh
cd /mnt/c/Users/dbsin/Projects/MiroFish-modified
backend/.venv/bin/python -m unittest discover -s backend/tests -v
cd frontend
npm run build
```

The offline suite produces a new recording with committed SQLite WAL data, then
replays all stages through Flask while live stage handlers are tripwires. It
checks timing, intermediate profiles/config/sections/actions/posts, incremental
logs, pause/resume, persisted restart state, idempotence, fresh IDs, unchanged
bundle bytes, missing/invalid bundles, saved retrieval and interaction dispatch.
It also checks the interaction loader with an environment that rejects steps.
Live LLM/OASIS services are replaced in these tests.

For a full service check, record a small opted-in live run in your normal WSL
environment, complete its report, stop the backend and disable access to the
analysis services. Restart and replay each stage. Then restore the live LLM,
ask a new interview question and report-chat question, and confirm that saved
posts remain available and no new simulation-round events are written. Replay
the same bundle again to check that its interaction history and usage begin
independently.
