"""Offline recording/replay integration tests; all live stage handlers are tripwires.

Run: python -m unittest discover -s backend/tests -v
Uses an isolated package namespace so optional live-service dependencies are not
required to test playback. Production recording, models, dispatch and API code
are loaded unchanged from disk.
"""
from contextlib import closing
import ast
import asyncio
import csv
from enum import Enum
import importlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1] / "app"
NAMESPACE = "saved_simulation_test_app"
for package in ("", ".services", ".models", ".api"):
    module = types.ModuleType(NAMESPACE + package)
    module.__path__ = [str(APP_DIR / package.lstrip(".")) if package else str(APP_DIR)]
    sys.modules[module.__name__] = module

Config = importlib.import_module(NAMESPACE + ".config").Config
recording = importlib.import_module(NAMESPACE + ".services.recording")
replay = importlib.import_module(NAMESPACE + ".services.replay")
project_module = importlib.import_module(NAMESPACE + ".models.project")
task_module = importlib.import_module(NAMESPACE + ".models.task")
tools_module = importlib.import_module(NAMESPACE + ".services.replay_tools")
api = importlib.import_module(NAMESPACE + ".api.recordings")
from flask import Flask


class Status(Enum):
    COMPLETED = "completed"


class State:
    def __init__(self, data):
        self.data = data
        self.__dict__.update(data)

    def to_dict(self):
        return dict(self.data)

    to_detail_dict = to_dict


class SavedSimulationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.root = root
        self.patches = [
            patch.object(Config, "UPLOAD_FOLDER", str(root)),
            patch.object(Config, "OASIS_SIMULATION_DATA_DIR", str(root / "simulations")),
            patch.object(project_module.ProjectManager, "PROJECTS_DIR", str(root / "projects")),
            patch.object(project_module.ProjectManager, "get_aggregated_usage", return_value={"total_tokens": 0}),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        # Finish/create use ReportManager only to resolve existing artifact paths.
        manager = type("ReportManager", (), {
            "_get_report_folder": staticmethod(lambda rid: str(root / "reports" / rid)),
            "_get_translated_html_path": staticmethod(lambda rid: str(root / "reports" / rid / "full_report_en.html")),
            "_get_report_markdown_path": staticmethod(lambda rid: str(root / "reports" / rid / "full_report.md")),
        })
        report_module = types.ModuleType(NAMESPACE + ".services.report_agent")
        report_module.ReportManager = manager
        graph_module = types.ModuleType(NAMESPACE + ".services.graph_backend")
        graph_module.get_graph_builder = lambda: types.SimpleNamespace(get_graph_data=lambda gid: self.graph)
        self.modules = patch.dict(sys.modules, {
            report_module.__name__: report_module, graph_module.__name__: graph_module,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.now = 1000.0
        clock = patch.object(recording.time, "time", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.app = Flask(__name__)
        self.app.config["SAVED_SIMULATION_STAGE_SECONDS"] = 10
        self.app.before_request(replay.dispatch_replay)
        self.app.after_request(replay.decorate_replay_response)
        self.app.register_blueprint(api.recordings_bp, url_prefix="/api")
        self.live_calls = []
        # Register the real routes and endpoint names without importing live
        # services. Reaching any stage handler is a failure during playback.
        for name in ("graph", "simulation", "report"):
            tree = ast.parse((APP_DIR / "api" / (name + ".py")).read_text(encoding="utf-8"))
            for fn in tree.body:
                if not isinstance(fn, ast.FunctionDef):
                    continue
                for decorator in fn.decorator_list:
                    if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "route":
                        route = ast.literal_eval(decorator.args[0])
                        methods = next(ast.literal_eval(k.value) for k in decorator.keywords if k.arg == "methods")
                        endpoint = name + "." + fn.name
                        def tripwire(endpoint=endpoint, **kwargs):
                            self.live_calls.append(endpoint)
                            return {"success": False, "error": "Live handler invoked"}, 599
                        self.app.add_url_rule("/api/" + name + route, endpoint, tripwire, methods=methods)
        self.client = self.app.test_client()
        self.make_recording()

    def advance(self, seconds=1):
        self.now += seconds

    def make_recording(self):
        pm = project_module.ProjectManager
        self.project = pm.create_project("Recorded example")
        self.project.record_run = True
        self.project.simulation_requirement = "Predict saved reactions"
        self.project.files = [{"filename": "source.txt", "size": 12}]
        (Path(pm._get_project_dir(self.project.project_id)) / "files" / "source.txt").write_text("Saved source", encoding="utf-8")
        pm.save_project(self.project)
        pm.save_extracted_text(self.project.project_id, "Alice discusses the local school. Source text saved for chat.")
        self.advance()
        self.project.ontology = {"entity_types": [{"name": "Person"}], "edge_types": []}
        self.project.status = project_module.ProjectStatus.ONTOLOGY_GENERATED
        pm.save_project(self.project)
        self.project.graph_id = "source_graph"
        self.project.graph_build_task_id = "source_graph_task"
        self.graph = {"graph_id": "source_graph", "nodes": [{"uuid": "node_alice", "name": "Alice", "labels": ["Person"]}],
                      "edges": [{"uuid": "edge_a", "source_node_uuid": "node_alice", "target_node_uuid": "node_alice", "fact": "Alice discusses school"}],
                      "node_count": 1, "edge_count": 1}
        self.advance()
        recording.RecordingStore.graph(self.project.project_id, {"nodes": [], "edges": [], "node_count": 0, "edge_count": 0})
        self.advance()
        recording.RecordingStore.graph(self.project.project_id, self.graph)
        self.project.status = project_module.ProjectStatus.GRAPH_COMPLETED
        pm.save_project(self.project)
        self.sim = State({"simulation_id": "source_sim", "project_id": self.project.project_id, "graph_id": "source_graph",
                          "status": "created", "enable_twitter": True, "enable_reddit": True, "entities_count": 2})
        self.sim_dir = self.root / "simulations" / "source_sim"
        self.sim_dir.mkdir(parents=True)
        recording.atomic_json(self.sim_dir / "state.json", self.sim.to_dict())
        recording.RecordingStore.create(self.sim)
        self.bundle = recording.RecordingStore.bundle("source_sim")
        self.recording_id = self.bundle.name
        profiles = [{"agent_id": 0, "username": "alice", "persona": "saved persona"}, {"agent_id": 1, "username": "bob", "persona": "saved persona"}]
        recording.RecordingStore.preparation("source_sim", "entities_filtered", {"entities": self.graph["nodes"]})
        self.advance()
        recording.atomic_json(self.sim_dir / "reddit_profiles.json", profiles[:1])
        with (self.sim_dir / "twitter_profiles.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=profiles[0].keys())
            writer.writeheader()
            writer.writerows(profiles[:1])
        recording.RecordingStore.preparation("source_sim", "progress")
        self.advance()
        recording.atomic_json(self.sim_dir / "reddit_profiles.json", profiles)
        with (self.sim_dir / "twitter_profiles.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=profiles[0].keys())
            writer.writeheader()
            writer.writerows(profiles)
        recording.atomic_json(self.sim_dir / "simulation_config.json", {"simulation_id": "source_sim", "project_id": self.project.project_id, "graph_id": "source_graph", "agent_configs": profiles,
                                                                      "time_config": {"total_simulation_hours": 1, "minutes_per_round": 30}})
        self.sim.data.update(status="ready", config_generated=True, profiles_count=2)
        recording.atomic_json(self.sim_dir / "state.json", self.sim.to_dict())
        self.advance()
        recording.RecordingStore.preparation("source_sim", "completed")
        self.databases = []
        for platform in ("twitter", "reddit"):
            db = sqlite3.connect(self.sim_dir / (platform + "_simulation.db"))
            self.databases.append(db)
            self.addCleanup(db.close)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE post(post_id INTEGER PRIMARY KEY, content TEXT, created_at TEXT)")
            db.execute("CREATE TABLE comment(comment_id INTEGER PRIMARY KEY, post_id INTEGER, content TEXT, created_at TEXT)")
            db.commit()
        run = State({"simulation_id": "source_sim", "runner_status": "running", "current_round": 0, "total_rounds": 2})
        recording.RecordingStore.simulation(run)
        for number in (1, 2):
            self.advance()
            for platform, db in zip(("twitter", "reddit"), self.databases):
                db.execute("INSERT INTO post VALUES (?, ?, ?)", (number, "Saved post " + str(number), str(number)))
                db.commit()
                recording.RecordingStore.simulation(run, "action", {"round_num": number, "agent_id": number - 1, "platform": platform,
                                                                   "action_type": "CREATE_POST", "agent_name": "Alice", "timestamp": str(self.now), "action_args": {"content": "Saved post " + str(number)}})
            run.data["current_round"] = number
            recording.RecordingStore.simulation(run)
        self.advance()
        run.data["runner_status"] = "completed"
        recording.atomic_json(self.sim_dir / "run_state.json", run.to_dict())
        recording.RecordingStore.simulation(run)
        self.report = State({"report_id": "source_report", "simulation_id": "source_sim", "graph_id": "source_graph",
                             "simulation_requirement": "Predict saved reactions", "status": "completed", "markdown_content": "# Saved report\nResults",
                             "outline": {"title": "Saved report", "summary": "Saved summary", "sections": [{"title": "Results", "content": "Results"}]}})
        self.report.status = Status.COMPLETED
        folder = self.root / "reports" / "source_report"
        folder.mkdir(parents=True)
        recording.atomic_json(folder / "meta.json", self.report.to_dict())
        (folder / "full_report.md").write_text(self.report.markdown_content, encoding="utf-8")
        (folder / "full_report_en.html").write_text("<h1>Saved translation</h1>", encoding="utf-8")
        recording.RecordingStore.report_event("source_sim", "source_report", "started", {})
        self.advance()
        outline = {"title": "Saved report", "summary": "Saved summary", "sections": [{"title": "Results", "content": ""}]}
        recording.RecordingStore.report_progress("source_report", "outline", outline)
        recording.RecordingStore.report_progress("source_report", "agent_log", {"action": "planning_complete", "details": {"outline": outline}})
        recording.RecordingStore.report_progress("source_report", "console_log", "Planning complete")
        self.advance()
        recording.RecordingStore.report_progress("source_report", "sections", [{"filename": "section_01.md", "section_index": 1, "content": "Saved section"}])
        recording.RecordingStore.report_progress("source_report", "agent_log", {"action": "section_complete", "section_index": 1, "details": {"content": "Saved section"}})
        self.advance()
        recording.RecordingStore.report_progress("source_report", "agent_log", {"action": "report_complete", "details": {}})
        recording.RecordingStore.finish(self.report)
        self.assertTrue(recording.read_json(self.bundle / "manifest.json")["frozen"])

    def create(self, seconds=10):
        self.app.config["SAVED_SIMULATION_STAGE_SECONDS"] = seconds
        response = self.client.post("/api/replays", json={"recording_id": self.recording_id})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json["data"]

    def ready_through(self, stage, seconds=10):
        session = self.create(seconds)
        self.advance(seconds)
        operations = [("graph", "/api/graph/build", "project_id"), ("preparation", "/api/simulation/prepare", "simulation_id"),
                      ("simulation", "/api/simulation/start", "simulation_id"), ("report", "/api/report/generate", "simulation_id")]
        for name, path, key in operations:
            response = self.client.post(path, json={key: session[key]})
            self.assertEqual(response.status_code, 200, response.json)
            if name == stage:
                return session
            self.advance(seconds)
        return session

    def test_replay_all_stages_offline_and_translation(self):
        session = self.ready_through("report")
        self.advance(10)
        for path in (f'/api/graph/project/{session["project_id"]}', f'/api/graph/data/{session["graph_id"]}',
                     f'/api/simulation/{session["simulation_id"]}/profiles', f'/api/simulation/{session["simulation_id"]}/config',
                     f'/api/simulation/{session["simulation_id"]}/run-status/detail', f'/api/report/{session["report_id"]}',
                     f'/api/report/{session["report_id"]}/sections', f'/api/report/{session["report_id"]}/agent-log',
                     f'/api/report/{session["report_id"]}/console-log', f'/api/report/{session["report_id"]}/translate',
                     f'/api/simulation/{session["simulation_id"]}/posts', f'/api/simulation/{session["simulation_id"]}/comments'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, (path, response.json))
            self.assertEqual(response.json["run_mode"], "saved")
        self.assertEqual(self.live_calls, [])

    def test_intermediate_profiles_config_and_pause_restart(self):
        session = self.ready_through("preparation")
        sid = session["simulation_id"]
        self.advance(4)
        self.assertEqual(self.client.get(f"/api/simulation/{sid}/profiles/realtime").json["data"]["count"], 1)
        self.assertIsNone(self.client.get(f"/api/simulation/{sid}/config").json["data"])
        self.client.post(f'/api/replays/{session["replay_id"]}/stop')
        self.advance(100)
        importlib.reload(replay)
        persisted = replay.ReplayStore.lookup(sid)
        self.assertEqual(replay.ReplayStore.position(persisted, "preparation"), .4)
        response = self.client.post(f'/api/replays/{session["replay_id"]}/resume')
        self.assertEqual(response.status_code, 200)
        self.advance(6)
        self.assertEqual(self.client.get(f"/api/simulation/{sid}/profiles").json["data"]["count"], 2)
        self.assertIsNotNone(self.client.get(f"/api/simulation/{sid}/config").json["data"])

    def test_action_visibility_pagination_and_database_visibility_share_clock(self):
        session = self.ready_through("simulation")
        sid = session["simulation_id"]
        self.advance(4)
        response = self.client.get(f"/api/simulation/{sid}/run-status/detail").json["data"]
        self.assertEqual(len(response["all_actions"]), 2)
        self.assertEqual(response["current_round"], 1)
        self.assertEqual(self.client.get(f"/api/simulation/{sid}/actions?limit=1&offset=1").json["data"]["count"], 1)
        posts = self.client.get(f"/api/simulation/{sid}/posts?platform=twitter").json["data"]
        self.assertEqual(posts["total"], 1)
        self.assertEqual(posts["posts"][0]["content"], "Saved post 1")
        self.client.post("/api/simulation/stop", json={"simulation_id": sid})
        self.advance(100)
        self.assertEqual(len(self.client.get(f"/api/simulation/{sid}/run-status/detail").json["data"]["all_actions"]), 2)
        self.client.post("/api/simulation/start", json={"simulation_id": sid})
        self.advance(6)
        self.assertEqual(len(self.client.get(f"/api/simulation/{sid}/run-status/detail").json["data"]["all_actions"]), 4)

    def test_sections_and_incremental_logs_are_gated(self):
        session = self.ready_through("report")
        rid = session["report_id"]
        self.advance(4)
        self.assertEqual(self.client.get(f"/api/report/{rid}/sections").json["data"]["sections"], [])
        self.assertEqual(self.client.get(f"/api/report/{rid}").json["data"]["markdown_content"], "")
        logs = self.client.get(f"/api/report/{rid}/agent-log").json["data"]
        self.assertEqual(logs["total_lines"], 1)
        self.assertEqual(self.client.get(f"/api/report/{rid}/agent-log?from_line=1").json["data"]["logs"], [])
        self.advance(3)
        self.assertEqual(len(self.client.get(f"/api/report/{rid}/sections").json["data"]["sections"]), 1)
        self.assertEqual(len(self.client.get(f"/api/report/{rid}/agent-log?from_line=1").json["data"]["logs"]), 1)
        self.advance(3)
        self.assertTrue(self.client.get(f"/api/report/check/{session['simulation_id']}").json["data"]["interview_unlocked"])

    def test_zero_duration_idempotence_and_fixed_config(self):
        session = self.ready_through("report", 0)
        first = self.client.post("/api/report/generate", json={"simulation_id": session["simulation_id"]}).json["data"]
        second = self.client.post("/api/report/generate", json={"simulation_id": session["simulation_id"]}).json["data"]
        self.assertEqual(first["task_id"], second["task_id"])
        self.app.config["SAVED_SIMULATION_STAGE_SECONDS"] = 90
        self.assertTrue(self.client.get(f'/api/replays/{session["replay_id"]}').json["data"]["completed"])

    def test_two_replays_own_state_and_preserve_graph_agent_ids(self):
        before = {str(p.relative_to(self.bundle)): p.read_bytes() for p in self.bundle.rglob("*") if p.is_file()}
        one, two = self.create(0), self.create(0)
        for key in ("project_id", "graph_id", "simulation_id", "report_id", "replay_id"):
            self.assertNotEqual(one[key], two[key])
        for session in (one, two):
            report_meta = recording.read_json(self.root / "reports" / session["report_id"] / "meta.json")
            self.assertEqual(report_meta["simulation_id"], session["simulation_id"])
            profiles = recording.read_json(self.root / "simulations" / session["simulation_id"] / "reddit_profiles.json")
            self.assertEqual(profiles[0]["agent_id"], 0)
            graph = replay.ReplayView(replay.ReplayStore.lookup(session["replay_id"])).artifact("graph.json")
            self.assertEqual(graph["edges"][0]["source_node_uuid"], "node_alice")
        db_path = self.root / "simulations" / one["simulation_id"] / "twitter_simulation.db"
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute("INSERT INTO post VALUES (3, 'New session state', '3')")
            conn.commit()
        with closing(sqlite3.connect(self.root / "simulations" / two["simulation_id"] / "twitter_simulation.db")) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM post").fetchone()[0], 2)
        after = {str(p.relative_to(self.bundle)): p.read_bytes() for p in self.bundle.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_restrictions_and_missing_sessions_fail_closed(self):
        session = self.create(0)
        for path, body in (("/api/simulation/start", {"simulation_id": session["simulation_id"], "max_rounds": 3}),
                           ("/api/report/generate", {"simulation_id": session["simulation_id"], "force_regenerate": True}),
                           (f'/api/graph/project/{session["project_id"]}/reset', {}),
                           ("/api/report/chat", {"simulation_id": session["simulation_id"], "message": "Early question"})):
            response = self.client.post(path, json=body)
            self.assertEqual(response.status_code, 400, response.json)
        response = self.client.get("/api/simulation/rsim_missing/profiles")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.live_calls, [])

    def test_stage_order_and_cross_session_identity(self):
        one, two = self.create(), self.create()
        early = self.client.post("/api/simulation/start", json={"simulation_id": one["simulation_id"]})
        self.assertEqual(early.status_code, 400)
        mixed = self.client.post("/api/simulation/create", json={"project_id": one["project_id"], "graph_id": two["graph_id"]})
        self.assertEqual(mixed.status_code, 400)

    def test_corrupt_incomplete_unsupported_and_invalid_timing(self):
        original = recording.read_json(self.bundle / "manifest.json")
        for changes, message in (({"version": 999}, "Unsupported"), ({"frozen": False, "stages": {}}, "missing stages")):
            recording.atomic_json(self.bundle / "manifest.json", {**original, **changes})
            response = self.client.post("/api/replays", json={"recording_id": self.recording_id})
            self.assertEqual(response.status_code, 400)
            self.assertIn(message, response.json["error"])
        recording.atomic_json(self.bundle / "manifest.json", original)
        for seconds in (-1, float("inf"), float("nan")):
            with self.assertRaisesRegex(ValueError, "finite and nonnegative"):
                replay.ReplayStore.create(self.recording_id, seconds)
        (self.bundle / "graph.json").write_text("{}", encoding="utf-8")
        response = self.client.post("/api/replays", json={"recording_id": self.recording_id})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Corrupt recording artifact", response.json["error"])
        self.assertFalse(next(i for i in recording.RecordingStore.list()["recordings"] if i["recording_id"] == self.recording_id)["ready"])

    def test_recording_failure_is_visible_and_does_not_raise(self):
        with patch.object(recording, "copy_artifacts", side_effect=OSError("Disk unavailable")):
            self.assertIsNone(recording.RecordingStore.project(self.project))
        failures = recording.RecordingStore.list()["errors"]
        self.assertTrue(any("Disk unavailable" in e["error"] for e in failures))

    def test_unrecorded_live_project_is_not_captured(self):
        project = project_module.ProjectManager.create_project("Unrecorded")
        project_module.ProjectManager.save_project(project)
        self.assertFalse((recording.RecordingStore.root() / "drafts" / project.project_id).exists())
        self.assertFalse(project.record_run)

    def test_no_new_recordings_for_replay_and_saved_retrieval(self):
        session = self.ready_through("report", 0)
        count = len(recording.RecordingStore.list()["recordings"])
        saved_project = project_module.ProjectManager.get_project(session["project_id"])
        project_module.ProjectManager.save_project(saved_project)
        self.assertEqual(len(recording.RecordingStore.list()["recordings"]), count)
        tools = tools_module.SavedToolsService(replay.ReplayStore.lookup(session["replay_id"]))
        result = tools.quick_search(session["graph_id"], "school").to_dict()
        self.assertIn("school", result["source_passages"][0])
        self.assertEqual(result["nodes"][0]["uuid"], "node_alice")

    def test_live_interaction_only_after_report_and_responses_persist(self):
        session = self.ready_through("report", 0)
        self.app.view_functions["report.chat_with_report_agent"] = lambda: {"success": True, "data": {"response": "A new live answer"}}
        self.app.view_functions["simulation.interview_agent"] = lambda: {"success": True, "data": {"response": "A new interview"}}
        runner = types.ModuleType(NAMESPACE + ".services.simulation_runner")
        starts = []
        runner.SimulationRunner = type("Runner", (), {"ensure_interaction_runner": staticmethod(starts.append)})
        with patch.dict(sys.modules, {runner.__name__: runner}):
            chat = self.client.post("/api/report/chat", json={"simulation_id": session["simulation_id"], "message": "New question"})
            interview = self.client.post("/api/simulation/interview", json={"simulation_id": session["simulation_id"], "agent_id": 0, "prompt": "New question"})
        self.assertEqual(chat.status_code, 200)
        self.assertEqual(interview.status_code, 200)
        self.assertEqual(starts, [session["simulation_id"]])
        interactions = replay.ReplayStore.root() / session["replay_id"] / "interactions.jsonl"
        self.assertEqual(len(interactions.read_text(encoding="utf-8").splitlines()), 2)
        self.assertEqual(self.client.get(f'/api/simulation/{session["simulation_id"]}/posts').json["data"]["total"], 2)


class InteractionLoaderTests(unittest.TestCase):
    def test_loader_keeps_posts_and_never_executes_rounds_or_seeding(self):
        script = APP_DIR.parents[0] / "scripts" / "run_parallel_simulation.py"
        tree = ast.parse(script.read_text(encoding="utf-8"))
        fn = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "load_saved_interaction_platform")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            recording.atomic_json(path / "run_state.json", {"twitter_current_round": 2})
            for name in ("twitter_profiles.csv", "reddit_profiles.json"):
                (path / name).write_text("saved profiles", encoding="utf-8")
            for platform in ("twitter", "reddit"):
                with closing(sqlite3.connect(path / (platform + "_simulation.db"))) as conn:
                    conn.execute("CREATE TABLE post(content TEXT)")
                    conn.execute("INSERT INTO post VALUES ('preserved post')")
                    conn.commit()
            calls = []
            class Env:
                platform = types.SimpleNamespace(sandbox_clock=types.SimpleNamespace(time_step=0))
                async def reset(self):
                    calls.append("reattach")
                async def step(self, *args):
                    raise AssertionError("No simulation steps during loading")
            async def graph(**kwargs):
                calls.append("load_profiles")
                return object()
            globals = {"os": importlib.import_module("os"), "json": json, "PlatformSimulation": lambda: types.SimpleNamespace(),
                       "create_model": lambda *a, **k: object(), "generate_twitter_agent_graph": graph,
                       "generate_reddit_agent_graph": graph, "TWITTER_ACTIONS": [], "REDDIT_ACTIONS": [],
                       "_apply_oasis_patches": lambda: calls.append("preserve_database"),
                       "oasis": types.SimpleNamespace(make=lambda **kwargs: Env(), DefaultPlatformType=types.SimpleNamespace(TWITTER="twitter", REDDIT="reddit"))}
            exec(compile(ast.Module(body=[fn], type_ignores=[]), str(script), "exec"), globals)
            for platform in ("twitter", "reddit"):
                asyncio.run(globals["load_saved_interaction_platform"]({}, folder, platform))
                with closing(sqlite3.connect(path / (platform + "_simulation.db"))) as conn:
                    self.assertEqual(conn.execute("SELECT content FROM post").fetchone()[0], "preserved post")
            self.assertEqual(calls.count("reattach"), 2)


if __name__ == "__main__":
    unittest.main()
