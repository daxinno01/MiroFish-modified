"""Verify replay dispatch and recording hooks in the full WSL application."""
import importlib
import io
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import test_saved_simulations as fixture_module


@unittest.skipUnless(os.name == "posix", "Full application dependencies are installed in WSL")
class FullApplicationReplayTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app import create_app
        from app.config import Config
        from app.models.project import ProjectManager
        from app.services.simulation_manager import SimulationManager
        from app.services.simulation_runner import SimulationRunner
        from app.services.report_agent import ReportManager
        self.config = Config
        self.pm = ProjectManager
        self.runner = SimulationRunner
        self.reports = ReportManager
        self.graph_api = importlib.import_module("app.api.graph")
        self.sim_api = importlib.import_module("app.api.simulation")
        self.report_api = importlib.import_module("app.api.report")
        self.recording = importlib.import_module("app.services.recording")
        self.fixture = fixture_module.SavedSimulationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        root = self.fixture.root
        for owner, key, value in (
            (Config, "UPLOAD_FOLDER", str(root)), (Config, "OASIS_SIMULATION_DATA_DIR", str(root / "simulations")),
            (Config, "SAVED_SIMULATION_STAGE_SECONDS", 0), (Config, "DEBUG", False),
            (ProjectManager, "PROJECTS_DIR", str(root / "projects")),
            (SimulationManager, "SIMULATION_DATA_DIR", str(root / "simulations")),
            (SimulationRunner, "RUN_STATE_DIR", str(root / "simulations")),
            (ReportManager, "REPORTS_DIR", str(root / "reports")),
        ):
            p = patch.object(owner, key, value)
            p.start()
            self.addCleanup(p.stop)
        self.app = create_app(Config)
        self.client = self.app.test_client()

    def test_full_flask_dispatch_never_enters_live_generation_and_chat_uses_saved_context(self):
        from app.services.report_agent import ReportAgent
        from app.services.replay_tools import SavedToolsService
        from app.services.simulation_manager import SimulationManager
        tripwire = AssertionError("Live analysis service invoked during replay")
        with patch.object(self.config, "validate", side_effect=tripwire), \
             patch.object(self.graph_api, "get_graph_builder", side_effect=tripwire), \
             patch.object(self.sim_api, "get_entity_reader", side_effect=tripwire), \
             patch.object(SimulationManager, "prepare_simulation", side_effect=tripwire), \
             patch.object(self.runner, "start_simulation", side_effect=tripwire), \
             patch.object(ReportAgent, "generate_report", side_effect=tripwire):
            response = self.client.post("/api/replays", json={"recording_id": self.fixture.recording_id})
            self.assertEqual(response.status_code, 200, response.json)
            session = response.json["data"]
            for path, key in (("/api/graph/build", "project_id"), ("/api/simulation/create", "project_id"),
                              ("/api/simulation/prepare", "simulation_id"), ("/api/simulation/start", "simulation_id"),
                              ("/api/report/generate", "simulation_id")):
                response = self.client.post(path, json={key: session[key]})
                self.assertEqual(response.status_code, 200, (path, response.json))
            for path in (f'/api/graph/data/{session["graph_id"]}', f'/api/simulation/{session["simulation_id"]}/config',
                         f'/api/report/{session["report_id"]}/agent-log', f'/api/report/{session["report_id"]}/translate'):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, (path, response.json))
            report = self.reports.get_report_by_simulation(session["simulation_id"])
            self.assertEqual(report.report_id, session["report_id"])
            self.assertIn("Saved report", report.markdown_content)

            def chat(agent, **kwargs):
                self.assertIsInstance(agent.zep_tools, SavedToolsService)
                result = agent.zep_tools.quick_search(agent.graph_id, "school").to_dict()
                self.assertIn("school", result["source_passages"][0])
                self.assertEqual(kwargs["message"], "New live question")
                return {"response": "New live answer", "sources": result["facts"], "tool_calls": []}

            class LiveClient:
                def __init__(self, **kwargs):
                    self.step = "step5"

            with patch("app.services.report_agent.LLMClient", LiveClient), patch.object(ReportAgent, "chat", chat):
                response = self.client.post("/api/report/chat", json={"simulation_id": session["simulation_id"], "message": "New live question"})
                self.assertEqual(response.status_code, 200, response.json)
                self.assertEqual(response.json["data"]["response"], "New live answer")
            with patch.object(self.runner, "ensure_interaction_runner") as launch, \
                 patch.object(self.runner, "check_env_alive", return_value=True), \
                 patch.object(self.runner, "interview_agent", return_value={"success": True, "response": "New interview answer"}) as interview:
                response = self.client.post("/api/simulation/interview", json={"simulation_id": session["simulation_id"], "agent_id": 0, "prompt": "New question"})
                self.assertEqual(response.status_code, 200, response.json)
                launch.assert_called_once_with(session["simulation_id"])
                interview.assert_called_once()
            response = self.client.get(f'/api/simulation/{session["simulation_id"]}/posts')
            self.assertEqual(response.json["data"]["total"], 2)

    def test_opt_in_upload_uses_real_project_and_ontology_recording_hooks(self):
        ontology = {"entity_types": [{"name": "Person"}], "edge_types": [], "analysis_summary": "Saved analysis"}
        with patch.object(self.graph_api, "OntologyGenerator", return_value=types.SimpleNamespace(generate=lambda **kwargs: ontology)), \
             patch.object(self.graph_api, "MultimodalProcessor", return_value=object()):
            response = self.client.post("/api/graph/ontology/generate", data={
                "files": (io.BytesIO(b"Alice discusses the school."), "source.txt"),
                "simulation_requirement": "Predict reactions", "project_name": "Real upload shape", "record_run": "true",
            }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200, response.json)
        pid = response.json["data"]["project_id"]
        project = self.pm.get_project(pid)
        self.assertTrue(project.record_run)
        self.assertEqual(project.files[0]["filename"], "source.txt")
        draft = self.recording.RecordingStore.root() / "drafts" / pid
        self.assertTrue((draft / "project" / "extracted_text.txt").exists())
        self.assertEqual(self.recording.read_json(draft / "project.json")["ontology"]["entity_types"][0]["name"], "Person")


if __name__ == "__main__":
    unittest.main()
