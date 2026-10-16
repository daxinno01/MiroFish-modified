"""Manual WSL check against the existing replay; leaves a replay-only dev stack running.

Run from the repository root with backend/.venv/bin/python:
  backend/tests/replay_runtime_check.py start
  backend/tests/replay_runtime_check.py exercise
"""
import concurrent.futures
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
STATE = Path("/tmp/mirofish-replay-dev.json")

SERVER = """
import logging
from unittest.mock import patch
from app import create_app
from app.config import Config
Config.DEBUG = False
Config.USE_MOCK_LLM = False
Config.LLM_API_KEY = None
Config.EXTRACT_API_KEY = None
Config.REPORT_API_KEY = None
Config.EMBEDDING_API_KEY = None
logging.basicConfig(level=logging.INFO)
app = create_app()
targets = [
    'app.api.graph.get_graph_builder',
    'app.api.graph.OntologyGenerator',
    'app.api.simulation.get_entity_reader',
    'app.services.simulation_manager.SimulationManager.prepare_simulation',
    'app.services.simulation_runner.SimulationRunner.start_simulation',
    'app.services.report_agent.ReportAgent.generate_report',
    'app.utils.llm_client.LLMClient.__init__',
]
for target in targets:
    patch(target, side_effect=AssertionError('Live analysis disabled during replay verification')).start()
app.run(host='0.0.0.0', port=5001, debug=False, threaded=True, use_reloader=False)
"""


def request(path, body=None, port=3000):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def wait_ready(url, timeout=150):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    raise AssertionError("Server did not start: " + url)


def start():
    env = dict(os.environ, BROWSER="none", USE_MOCK_LLM="false", FLASK_DEBUG="false")
    with Path("/tmp/mirofish-vite.log").open("a") as log:
        frontend = subprocess.Popen(["node", "node_modules/vite/bin/vite.js", "--host", "0.0.0.0"],
                                    cwd=ROOT / "frontend", env=env, stdout=log, stderr=log, start_new_session=True)
    wait_ready("http://127.0.0.1:3000")
    try:
        request("/api/replays/replay_missing")
        raise AssertionError("Expected backend connection failure")
    except urllib.error.HTTPError as exc:
        assert exc.code == 503, exc.code
        assert json.load(exc)["code"] == "BACKEND_UNAVAILABLE"
        print("Vite backend outage: structured 503 confirmed", flush=True)
    with Path("/tmp/mirofish-backend.log").open("a") as log:
        backend = subprocess.Popen([sys.executable, "-c", SERVER], cwd=ROOT / "backend", env=env,
                                   stdout=log, stderr=log, start_new_session=True)
    STATE.write_text(json.dumps({"backend": backend.pid, "frontend": frontend.pid}))
    wait_ready("http://127.0.0.1:5001/health")
    print("Replay-only development stack ready:", STATE.read_text(), flush=True)


def restart_backend():
    state = json.loads(STATE.read_text())
    cmdline = Path(f'/proc/{state["backend"]}/cmdline').read_bytes()
    assert b"Live analysis disabled during replay verification" in cmdline, "Refusing to stop an unrelated process"
    os.kill(state["backend"], signal.SIGTERM)
    for _ in range(100):
        try:
            request("/health", port=5001)
        except (OSError, urllib.error.URLError):
            break
        time.sleep(0.1)
    try:
        request("/api/replays/replay_missing")
        raise AssertionError("Backend did not stop")
    except urllib.error.HTTPError as exc:
        assert exc.code == 503
    with Path("/tmp/mirofish-backend.log").open("a") as log:
        backend = subprocess.Popen([sys.executable, "-c", SERVER], cwd=ROOT / "backend",
                                   stdout=log, stderr=log, start_new_session=True)
    state["backend"] = backend.pid
    STATE.write_text(json.dumps(state))
    wait_ready("http://127.0.0.1:5001/health")
    print("Backend restarted; proxy outage and recovery verified", flush=True)


def exercise():
    replay_folder = max((ROOT / "backend/uploads/replays").glob("replay_*"), key=lambda p: p.stat().st_mtime)
    path = replay_folder / "session.json"
    original = path.read_text()
    session = json.loads(original)
    pid = json.loads(STATE.read_text())["backend"]
    snapshots = []

    def rss():
        lines = Path(f"/proc/{pid}/status").read_text().splitlines()
        return int(next(line.split()[1] for line in lines if line.startswith("VmRSS:")))

    def check(path):
        result = request(path)
        assert result.get("success"), path
        return result

    common = [f'/api/graph/project/{session["project_id"]}',
              f'/api/graph/project/{session["project_id"]}/usage',
              f'/api/replays/{session["replay_id"]}']
    operations = [
        ("graph", "/api/graph/build", "project_id", [f'/api/graph/data/{session["graph_id"]}']),
        ("preparation", "/api/simulation/prepare", "simulation_id", [f'/api/simulation/{session["simulation_id"]}/profiles']),
        ("simulation", "/api/simulation/start", "simulation_id", [f'/api/simulation/{session["simulation_id"]}/actions?limit=5']),
        ("report", "/api/report/generate", "simulation_id", [f'/api/report/{session["report_id"]}/agent-log?from_line=5']),
    ]
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(check, common * 8))
            snapshots.append(rss())
            for stage, endpoint, key, paths in operations:
                assert request(endpoint, {key: session[key]})["success"]
                while True:
                    list(pool.map(check, (common + paths) * 2))
                    status = check(f'/api/replays/{session["replay_id"]}')["data"]
                    if status["stages"][stage]["position"] == 1:
                        break
                    time.sleep(1)
                snapshots.append(rss())
                wait_ready("http://127.0.0.1:5001/health", timeout=5)
                print(f"Existing replay stage={stage} port=5001 available rss_kib={snapshots[-1]}", flush=True)
        assert request(f'/api/replays/{session["replay_id"]}/stop', {})["success"]
        assert request(f'/api/replays/{session["replay_id"]}/resume', {})["success"]
        print("Existing replay verified with live services disabled; RSS KiB:", snapshots, flush=True)
        restart_backend()
        status = check(f'/api/replays/{session["replay_id"]}')["data"]
        assert status["completed"]
        check(f'/api/report/{session["report_id"]}/agent-log?from_line=5')
        print("Persisted replay and event index recovered after process restart", flush=True)
    finally:
        temp = path.with_suffix(".restore.tmp")
        temp.write_text(original)
        temp.replace(path)
        print("Original replay clock restored; recording and replay IDs preserved", flush=True)


if __name__ == "__main__":
    {"start": start, "exercise": exercise}[sys.argv[1]]()
