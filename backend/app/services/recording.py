"""Opt-in, producer-side recording. No live service is constructed by this module."""
from __future__ import annotations
from contextlib import closing

import functools
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
import uuid

from ..config import Config

STAGES = ("ontology", "graph", "preparation", "simulation", "report")
VERSION = 1
_lock = threading.RLock()
logger = logging.getLogger(__name__)


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def file_checksum(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_events(path):
    with Path(path).open("r", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def safe_capture(fn):
    """Recording errors are persisted and logged, never propagated to a live run."""
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            with _lock:
                return fn(*args, **kwargs)
        except Exception as exc:
            logger.exception("Recording failed in %s", fn.__name__)
            try:
                subject = args[1] if args and isinstance(args[0], type) and len(args) > 1 else (args[0] if args else "")
                subject = getattr(subject, "simulation_id", getattr(subject, "project_id", str(subject)))
                atomic_json(Path(Config.UPLOAD_FOLDER) / "recordings" / "errors" /
                            (uuid.uuid4().hex + ".json"),
                            {"producer": fn.__name__, "subject": str(subject),
                             "error": str(exc), "timestamp": time.time()})
            except Exception:
                logger.exception("Unable to persist recording error")
    return wrapped


def copy_artifacts(source, target):
    """Back up SQLite through its API, including committed WAL contents."""
    source, target = Path(source), Path(target)
    if not source.exists():
        return
    for item in source.rglob("*"):
        if not item.is_file() or item.is_symlink():
            continue
        relative = item.relative_to(source)
        if any(p.startswith("ipc_") for p in relative.parts) or item.name in {
            "env_status.json", "usage.json"
        } or item.name.endswith(("-wal", "-shm", ".tmp")):
            continue
        dest = target / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        if item.suffix in {".db", ".sqlite", ".sqlite3"}:
            temp = dest.with_name(dest.name + ".tmp")
            with closing(sqlite3.connect(item.as_uri() + "?mode=ro", uri=True)) as src:
                with closing(sqlite3.connect(temp)) as dst:
                    src.backup(dst)
                    # A bundle database must be self contained. WAL mode would
                    # create sidecars even when the frozen backup is read.
                    dst.execute("PRAGMA journal_mode=DELETE")
            os.replace(temp, dest)
        else:
            temp = dest.with_name(dest.name + ".tmp")
            shutil.copyfile(item, temp)
            os.replace(temp, dest)


class RecordingStore:
    @classmethod
    @safe_capture
    def failure(cls, subject, error):
        atomic_json(cls.root() / "errors" / (uuid.uuid4().hex + ".json"),
                    {"subject": subject, "error": error, "timestamp": time.time()})

    @staticmethod
    def root():
        return Path(Config.UPLOAD_FOLDER) / "recordings"

    @classmethod
    def draft(cls, project_id):
        from ..models.project import ProjectManager
        project = ProjectManager.get_project(project_id)
        if not project or not project.record_run or project.run_mode == "saved":
            return None
        path = cls.root() / "drafts" / project_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def bundle(cls, simulation_id):
        if simulation_id.startswith("rsim_"):
            return None
        index = cls.root() / "index" / (simulation_id + ".json")
        if not index.exists():
            return None
        path = cls.root() / read_json(index)["recording_id"]
        if read_json(path / "manifest.json").get("frozen"):
            return None
        return path

    @staticmethod
    def event(path, stage, kind, data):
        if path is None:
            return
        path.mkdir(parents=True, exist_ok=True)
        with (path / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"time": time.time(), "stage": stage,
                                     "kind": kind, "data": data}, ensure_ascii=False) + "\n")

    @staticmethod
    def mark(path, stage):
        manifest = read_json(path / "manifest.json")
        manifest["stages"][stage] = True
        manifest["missing_stages"] = [s for s in STAGES if not manifest["stages"].get(s)]
        atomic_json(path / "manifest.json", manifest)

    @classmethod
    @safe_capture
    def project(cls, project):
        path = cls.draft(project.project_id)
        if path is None:
            return
        data = project.to_dict()
        atomic_json(path / "project.json", data)
        cls.event(path, "graph" if project.graph_build_task_id else "ontology", "project", data)
        from ..models.project import ProjectManager
        copy_artifacts(ProjectManager._get_project_dir(project.project_id), path / "project")

    @classmethod
    @safe_capture
    def graph(cls, project_id, graph_data):
        path = cls.draft(project_id)
        if path:
            atomic_json(path / "graph.json", graph_data)
            cls.event(path, "graph", "graph", graph_data)

    @classmethod
    @safe_capture
    def create(cls, state):
        draft = cls.draft(state.project_id)
        if draft is None or state.simulation_id.startswith("rsim_"):
            return
        recording_id = "rec_" + uuid.uuid4().hex[:12]
        path = cls.root() / recording_id
        shutil.copytree(draft, path)
        project = read_json(path / "project.json")
        stages = {s: False for s in STAGES}
        stages.update(ontology=bool(project.get("ontology")), graph=(path / "graph.json").exists())
        atomic_json(path / "manifest.json", {
            "version": VERSION, "recording_id": recording_id, "name": project["name"],
            "created_at": time.time(), "frozen": False, "stages": stages,
            "missing_stages": [s for s in STAGES if not stages[s]],
            "source": {"project_id": state.project_id, "graph_id": state.graph_id,
                       "simulation_id": state.simulation_id, "report_id": None}})
        atomic_json(cls.root() / "index" / (state.simulation_id + ".json"),
                    {"recording_id": recording_id})
        atomic_json(path / "simulation.json", state.to_dict())

    @classmethod
    @safe_capture
    def task(cls, task):
        meta = task.metadata
        path = cls.bundle(meta["simulation_id"]) if meta.get("simulation_id") else cls.draft(meta.get("project_id", ""))
        if path:
            stage = meta.get("recording_stage", "report" if task.task_type == "report_generate" else "preparation")
            cls.event(path, stage, "task", task.to_dict())
            if stage == "preparation":
                cls.preparation(meta["simulation_id"], "progress")

    @classmethod
    @safe_capture
    def preparation(cls, simulation_id, kind, data=None):
        path = cls.bundle(simulation_id)
        if path is None:
            return
        sim_dir = Path(Config.OASIS_SIMULATION_DATA_DIR) / simulation_id
        if kind == "entities_filtered":
            cls.event(path, "preparation", "entities_before_filtering", read_json(path / "graph.json"))
        if data is None:
            data = {}
            for filename in ("state.json", "reddit_profiles.json", "simulation_config.json"):
                if (sim_dir / filename).exists():
                    data[filename] = read_json(sim_dir / filename)
            if (sim_dir / "twitter_profiles.csv").exists():
                import csv
                with (sim_dir / "twitter_profiles.csv").open(encoding="utf-8", newline="") as f:
                    data["twitter_profiles.csv"] = list(csv.DictReader(f))
        cls.event(path, "preparation", kind, data)
        if kind == "completed":
            copy_artifacts(sim_dir, path / "simulation")
            atomic_json(path / "prepared.json", data)
            cls.mark(path, "preparation")

    @staticmethod
    def database_visibility(sim_dir):
        result = {}
        for platform in ("twitter", "reddit"):
            db = Path(sim_dir) / (platform + "_simulation.db")
            if db.exists():
                with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
                    for table in ("post", "comment"):
                        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                            result[platform + ":" + table] = conn.execute(f'SELECT COALESCE(MAX(rowid),0) FROM "{table}"').fetchone()[0]
        return result

    @classmethod
    @safe_capture
    def simulation(cls, state, kind="state", data=None):
        path = cls.bundle(state.simulation_id)
        if path is None:
            return
        if read_json(path / "manifest.json")["stages"]["simulation"]:
            return
        sim_dir = Path(Config.OASIS_SIMULATION_DATA_DIR) / state.simulation_id
        payload = data if data is not None else state.to_detail_dict()
        if kind == "state":
            payload["database_visibility"] = cls.database_visibility(sim_dir)
        cls.event(path, "simulation", kind, payload)
        if kind == "state" and payload["runner_status"] == "completed":
            manifest = read_json(path / "manifest.json")
            if not manifest["stages"]["simulation"]:
                copy_artifacts(sim_dir, path / "simulation")
                from .graph_backend import get_graph_builder
                graph = get_graph_builder().get_graph_data(manifest["source"]["graph_id"])
                atomic_json(path / "graph_simulation.json", graph)
                cls.event(path, "simulation", "graph", graph)
                atomic_json(path / "run.json", payload)
                cls.mark(path, "simulation")

    @classmethod
    @safe_capture
    def report_event(cls, simulation_id, report_id, kind, data):
        path = cls.bundle(simulation_id)
        if path is None:
            return
        manifest = read_json(path / "manifest.json")
        bound = manifest["source"].get("report_id")
        if bound and bound != report_id:
            return
        manifest["source"]["report_id"] = report_id
        atomic_json(path / "manifest.json", manifest)
        atomic_json(cls.root() / "reports" / (report_id + ".json"), {"simulation_id": simulation_id})
        cls.event(path, "report", kind, data)

    @classmethod
    @safe_capture
    def report_progress(cls, report_id, kind, data):
        index = cls.root() / "reports" / (report_id + ".json")
        if index.exists():
            cls.report_event(read_json(index)["simulation_id"], report_id, kind, data)

    @classmethod
    @safe_capture
    def memory_graph(cls, graph_id):
        from .graph_backend import get_graph_builder
        for index in (cls.root() / "index").glob("*.json"):
            path = cls.root() / read_json(index)["recording_id"]
            manifest = read_json(path / "manifest.json")
            if not manifest.get("frozen") and manifest["source"]["graph_id"] == graph_id:
                graph = get_graph_builder().get_graph_data(graph_id)
                cls.event(path, "simulation", "graph", graph)

    @classmethod
    @safe_capture
    def finish(cls, report):
        path = cls.bundle(report.simulation_id)
        if path is None or report.status.value != "completed":
            return
        cls.report_event(report.simulation_id, report.report_id, "completed", report.to_dict())
        from .report_agent import ReportManager
        from ..models.project import ProjectManager
        copy_artifacts(ReportManager._get_report_folder(report.report_id), path / "report")
        # Refresh memory after simulation, including all updates used to produce the report.
        from .graph_backend import get_graph_builder
        graph = get_graph_builder().get_graph_data(report.graph_id)
        atomic_json(path / "graph_final.json", graph)
        # Final context belongs to the completion milestone, not to report time.
        atomic_json(path / "report.json", report.to_dict())
        copy_artifacts(ProjectManager._get_project_dir(read_json(path / "manifest.json")["source"]["project_id"]), path / "project")
        cls.mark(path, "report")
        manifest = read_json(path / "manifest.json")
        if not manifest["missing_stages"]:
            manifest["checksums"] = {str(p.relative_to(path)).replace("\\", "/"): file_checksum(p)
                                     for p in path.rglob("*") if p.is_file() and p.name != "manifest.json"}
            manifest["frozen"] = True
            atomic_json(path / "manifest.json", manifest)

    @classmethod
    def validate(cls, recording_id):
        if not isinstance(recording_id, str) or not recording_id.startswith("rec_") or not recording_id[4:].isalnum():
            raise ValueError("Invalid recording_id")
        path = cls.root() / recording_id
        if not (path / "manifest.json").exists():
            raise FileNotFoundError("Recording not found")
        try:
            manifest = read_json(path / "manifest.json")
            if not isinstance(manifest, dict):
                raise ValueError("Corrupt recording manifest")
            if manifest.get("version") != VERSION:
                raise ValueError(f"Unsupported recording version: {manifest.get('version')}; expected {VERSION}")
            missing = [s for s in STAGES if not manifest.get("stages", {}).get(s)]
            if missing or not manifest.get("frozen"):
                raise ValueError("Incomplete recording; missing stages: " + ", ".join(missing or ["freeze"]))
            checksums = manifest.get("checksums", {})
            required = {"project.json", "simulation.json", "graph.json", "graph_final.json", "prepared.json", "run.json", "report.json", "events.jsonl", "project/extracted_text.txt", "simulation/state.json", "simulation/simulation_config.json", "report/meta.json", "report/full_report.md"}
            if not required.issubset(checksums):
                raise ValueError("Corrupt recording; missing artifacts: " + ", ".join(sorted(required - checksums.keys())))
            for name, checksum in checksums.items():
                item = (path / name).resolve()
                if not item.is_relative_to(path.resolve()) or not item.is_file() or file_checksum(item) != checksum:
                    raise ValueError("Corrupt recording artifact: " + name)
            for name in required - {"events.jsonl", "project/extracted_text.txt", "report/full_report.md"}:
                if not isinstance(read_json(path / name), dict):
                    raise ValueError("Corrupt recording artifact: " + name)
            source = manifest.get("source", {})
            if any(not isinstance(source.get(key), str) or not source[key] for key in ("project_id", "graph_id", "simulation_id", "report_id")):
                raise ValueError("Corrupt recording source mapping")
            for name, keys in (("project.json", ("project_id",)), ("simulation.json", ("project_id", "graph_id", "simulation_id")),
                               ("report.json", ("report_id", "graph_id", "simulation_id")), ("report/meta.json", ("report_id", "simulation_id"))):
                data = read_json(path / name)
                if any(data.get(key) != source[key] for key in keys):
                    raise ValueError("Corrupt recording reference in " + name)
            seen = set()
            for event in iter_events(path / "events.jsonl"):
                if (not isinstance(event, dict) or not isinstance(event.get("time"), (int, float))
                        or not math.isfinite(event["time"]) or event.get("stage") not in STAGES
                        or not isinstance(event.get("kind"), str) or "data" not in event):
                    raise ValueError("Corrupt recording event history")
                seen.add(event["stage"])
            for stage in STAGES:
                if stage not in seen:
                    raise ValueError("Corrupt recording; missing event history: " + stage)
            state = read_json(path / "simulation.json")
            for platform in ("reddit", "twitter"):
                if state.get("enable_" + platform):
                    profile = "reddit_profiles.json" if platform == "reddit" else "twitter_profiles.csv"
                    for artifact in ("simulation/" + profile, "simulation/" + platform + "_simulation.db"):
                        if artifact not in checksums:
                            raise ValueError("Corrupt recording; missing artifact: " + artifact)
                    with closing(sqlite3.connect((path / "simulation" / (platform + "_simulation.db")).as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
                        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                            raise ValueError("Corrupt simulation database: " + platform)
            return path, manifest
        except (KeyError, TypeError, json.JSONDecodeError, OSError, sqlite3.DatabaseError) as exc:
            raise ValueError("Corrupt recording: " + str(exc)) from exc

    @classmethod
    def list(cls):
        items = []
        for path in cls.root().glob("rec_*"):
            try:
                manifest = read_json(path / "manifest.json")
                error = None
                try:
                    cls.validate(path.name)
                except (ValueError, FileNotFoundError) as exc:
                    error = str(exc)
                items.append({**manifest, "ready": error is None, "error": error})
            except Exception as exc:
                items.append({"recording_id": path.name, "ready": False, "error": "Corrupt manifest: " + str(exc)})
        # Expose early recordings even if no simulation has been allocated yet.
        for path in (cls.root() / "drafts").glob("*"):
            try:
                project = read_json(path / "project.json")
                if not any(i.get("source", {}).get("project_id") == project["project_id"] for i in items):
                    complete = {"ontology": bool(project.get("ontology")), "graph": (path / "graph.json").exists()}
                    items.append({"recording_id": path.name, "name": project["name"], "ready": False,
                                  "missing_stages": [s for s in STAGES if not complete.get(s)], "error": "No simulation/report pair recorded yet"})
            except Exception:
                continue
        errors = []
        for path in (cls.root() / "errors").glob("*.json"):
            try:
                errors.append(read_json(path))
            except Exception:
                continue
        return {"recordings": sorted(items, key=lambda i: i.get("created_at", 0), reverse=True), "errors": errors}
