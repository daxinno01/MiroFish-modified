"""Regression coverage for bounded replay reads and concurrent index recovery."""
from concurrent.futures import ThreadPoolExecutor
import gc
import json
from pathlib import Path
import sqlite3
import tracemalloc
import unittest
from unittest.mock import patch

import test_saved_simulations as fixture_module

recording = fixture_module.recording
replay = fixture_module.replay


class ReplayMemoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.SavedSimulationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_large_recording_streams_and_ontology_requests_stay_bounded(self):
        fixture = self.fixture
        events = fixture.bundle / "events.jsonl"
        line = json.dumps({"stage": "simulation", "kind": "graph", "time": 1000,
                           "data": {"document": "x" * 65536}}) + "\n"
        with events.open("a", encoding="utf-8") as stream:
            for _ in range(1536):
                stream.write(line)
        manifest = recording.read_json(fixture.bundle / "manifest.json")
        manifest["checksums"]["events.jsonl"] = recording.file_checksum(events)
        recording.atomic_json(fixture.bundle / "manifest.json", manifest)
        original_read = Path.read_text

        def reject_event_read(path, *args, **kwargs):
            if path.name == "events.jsonl":
                raise AssertionError("Whole event file read")
            return original_read(path, *args, **kwargs)

        tracemalloc.start()
        try:
            with patch.object(Path, "read_text", reject_event_read), \
                 patch.object(Path, "read_bytes", side_effect=AssertionError("Whole artifact checksum read")):
                with self.assertLogs(replay.logger.name, level="INFO") as logs:
                    session = fixture.create()
                self.assertIn("Replay created", " ".join(logs.output))
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 12 * 1024 * 1024, f"Creation allocated {peak} bytes")

        # Usage endpoints fall through to their normal handlers without querying events.
        for name in ("graph.get_project_usage", "simulation.get_simulation_usage"):
            fixture.app.view_functions[name] = lambda **kwargs: {"success": True, "data": {"total_tokens": 0}}
        paths = [f'/api/graph/project/{session["project_id"]}',
                 f'/api/graph/project/{session["project_id"]}/usage',
                 f'/api/simulation/{session["simulation_id"]}/usage',
                 f'/api/replays/{session["replay_id"]}']

        def request(number):
            with fixture.app.test_client() as client:
                response = client.get(paths[number % len(paths)])
                self.assertEqual(response.status_code, 200, response.json)

        def rss():
            status = Path("/proc/self/status")
            if status.exists():
                return int(next(line.split()[1] for line in status.read_text().splitlines() if line.startswith("VmRSS:"))) * 1024
            return 0

        with patch.object(replay.EventIndex, "query", side_effect=AssertionError("Ontology polling queried event payloads")), \
             ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(request, range(80)))
            gc.collect()
            baseline = rss()
            samples = []
            for _ in range(4):
                list(pool.map(request, range(120)))
                gc.collect()
                samples.append(rss())
            self.assertLess(max(samples) - baseline, 12 * 1024 * 1024)
        print(f"Replay stress: events={events.stat().st_size} bytes, creation_peak={peak}, warmed_rss={baseline}, rss_samples={samples}")

    def test_atomic_index_recovery_and_queries_decode_only_selected_payloads(self):
        fixture = self.fixture
        session = fixture.ready_through("report")
        fixture.advance(10)
        session = replay.ReplayStore.lookup(session["replay_id"])
        index = replay.EventIndex(replay.ReplayStore.root() / session["replay_id"])
        index.path.unlink()

        def latest(_):
            return replay.ReplayView(session).latest("simulation", "state")["current_round"]

        with self.assertLogs("mirofish.replay.index", level="INFO") as logs, ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(latest, range(16))), [2] * 16)
        self.assertEqual(sum("Replay indexed" in line for line in logs.output), 1)
        self.assertFalse(list(index.folder.glob("*.tmp")))
        view = replay.ReplayView(session)
        decode = replay.EventIndex._payloads
        decoded = []

        def track(stream, rows):
            for payload in decode(stream, rows):
                decoded.append(payload)
                yield payload

        with patch.object(replay.EventIndex, "_payloads", staticmethod(track)):
            self.assertEqual(view.actions("twitter", count=True), 2)
            self.assertEqual(len(decoded), 0)
            page = view.actions("twitter", reverse=True, offset=1, limit=1)
            self.assertEqual(page[0]["round_num"], 1)
            self.assertEqual(len(decoded), 1)
            view.latest("simulation", "state")
            self.assertEqual(len(decoded), 2)
            view.logs("agent_log", offset=2)
            self.assertEqual(len(decoded), 3)

        # A failed build never publishes a partial database; the next request recovers.
        index.path.unlink()
        with patch.object(replay.EventIndex, "_payloads", staticmethod(decode)):
            with patch("json.loads", side_effect=ValueError("invalid")):
                with self.assertRaises(ValueError):
                    index.ensure()
        self.assertFalse(index.path.exists())
        self.assertFalse(list(index.folder.glob("*.tmp")))
        index.ensure()
        with sqlite3.connect(index.path) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
