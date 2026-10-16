"""Persisted playback clock and artifact projections shared by every replay API."""
from __future__ import annotations
from contextlib import closing

import copy
import functools
import json
import logging
from pathlib import Path
import threading
import time
import uuid

from ..config import Config
from .recording import RecordingStore, STAGES, atomic_json, copy_artifacts, read_json, iter_events
from .replay_index import EventIndex

_lock = threading.RLock()
logger = logging.getLogger("mirofish.replay")
REPLAY_PREFIXES = ("rproj_", "rgraph_", "rsim_", "rreport_", "rtask_", "replay_")


def log_creation_failure(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            logger.error("Replay creation failed error_type=%s", type(exc).__name__)
            raise
    return wrapped


def remap(value, mapping):
    """Only replace exact allocated IDs; agent IDs and node/edge references survive."""
    if isinstance(value, dict):
        return {k: remap(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [remap(v, mapping) for v in value]
    return mapping.get(value, value) if isinstance(value, str) else value


class ReplayStore:
    @staticmethod
    def root():
        return Path(Config.UPLOAD_FOLDER) / "replays"

    @classmethod
    def lookup(cls, identity):
        if not isinstance(identity, str) or not identity.startswith(REPLAY_PREFIXES):
            return None
        if not identity.replace("_", "").isalnum():
            raise ValueError("Invalid replay identity")
        index = cls.root() / "index" / (identity + ".json")
        if not index.exists():
            raise FileNotFoundError("Replay session data is missing for " + identity)
        session_id = read_json(index)["replay_id"]
        session = cls.root() / session_id / "session.json"
        if not session.exists():
            raise FileNotFoundError("Replay session data is missing for " + identity)
        return read_json(session)

    @classmethod
    def save(cls, session):
        atomic_json(cls.root() / session["replay_id"] / "session.json", session)

    @classmethod
    @log_creation_failure
    def create(cls, recording_id, stage_seconds=None):
        import math
        seconds = float(Config.SAVED_SIMULATION_STAGE_SECONDS if stage_seconds is None else stage_seconds)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("SAVED_SIMULATION_STAGE_SECONDS must be finite and nonnegative")
        with _lock:
            started = time.monotonic()
            logger.info("Replay creation started recording_id=%s", recording_id)
            try:
                bundle, manifest = RecordingStore.validate(recording_id)
            except Exception as exc:
                logger.error("Replay validation failed recording_id=%s error_type=%s", recording_id, type(exc).__name__)
                raise
            replay_id = "replay_" + uuid.uuid4().hex[:12]
            ids = {key: prefix + uuid.uuid4().hex[:12] for key, prefix in (
                ("project_id", "rproj_"), ("graph_id", "rgraph_"),
                ("simulation_id", "rsim_"), ("report_id", "rreport_"))}
            tasks = {stage: "rtask_" + uuid.uuid4().hex[:12] for stage in STAGES}
            mapping = {old: ids[key] for key, old in manifest["source"].items() if old}
            for event in iter_events(bundle / "events.jsonl"):
                if event["kind"] == "task":
                    mapping[event["data"]["task_id"]] = tasks[event["stage"]]
            folder = cls.root() / replay_id
            copy_artifacts(bundle, folder / "artifacts")
            EventIndex(folder).ensure()
            # Session owns its copies. Source bundles are never opened for writing.
            from ..models.project import ProjectManager
            from .report_agent import ReportManager
            copy_artifacts(bundle / "project", ProjectManager._get_project_dir(ids["project_id"]))
            copy_artifacts(bundle / "simulation", Path(Config.OASIS_SIMULATION_DATA_DIR) / ids["simulation_id"])
            copy_artifacts(bundle / "report", ReportManager._get_report_folder(ids["report_id"]))
            project = remap(read_json(bundle / "project.json"), mapping)
            project.update(record_run=False, run_mode="saved", replay_id=replay_id, graph_build_task_id=tasks["graph"])
            for file in project.get("files", []):
                # Current uploads expose filename/size; older projects may
                # additionally carry the physical saved filename and path.
                filename = file.get("saved_filename") or (Path(file["path"]).name if file.get("path") else None)
                if filename:
                    file["path"] = str(Path(ProjectManager._get_project_dir(ids["project_id"])) / "files" / filename)
            atomic_json(Path(ProjectManager._get_project_dir(ids["project_id"])) / "project.json", project)
            sim_dir = Path(Config.OASIS_SIMULATION_DATA_DIR) / ids["simulation_id"]
            for filename in ("state.json", "simulation_config.json", "run_state.json"):
                if (sim_dir / filename).exists():
                    data = remap(read_json(sim_dir / filename), mapping)
                    if filename == "run_state.json":
                        data["process_pid"] = None
                    atomic_json(sim_dir / filename, data)
            report_folder = Path(ReportManager._get_report_folder(ids["report_id"]))
            for filename in ("meta.json", "outline.json", "progress.json"):
                if (report_folder / filename).exists():
                    atomic_json(report_folder / filename, remap(read_json(report_folder / filename), mapping))
            session = {"replay_id": replay_id, "recording_id": recording_id, **ids,
                       "task_ids": tasks, "source": manifest["source"], "mapping": mapping,
                       "run_mode": "saved", "stage_seconds": seconds, "created_at": time.time(),
                       "current_stage": "ontology", "stages": {
                           stage: {"started_at": time.time() if stage == "ontology" else None,
                                   "elapsed": 0, "paused": False} for stage in STAGES}}
            cls.save(session)
            for identity in [replay_id, *ids.values(), *tasks.values()]:
                atomic_json(cls.root() / "index" / (identity + ".json"), {"replay_id": replay_id})
            logger.info("Replay created replay_id=%s recording_id=%s duration=%.3fs", replay_id, recording_id, time.monotonic() - started)
            return cls.status(session)

    @staticmethod
    def position(session, stage, now=None):
        clock = session["stages"][stage]
        if clock["started_at"] is None:
            return 0.0
        seconds = session["stage_seconds"]
        if seconds == 0:
            return 1.0
        elapsed = clock["elapsed"]
        if not clock["paused"]:
            elapsed += max(0, (time.time() if now is None else now) - clock["started_at"])
        return min(1.0, elapsed / seconds)

    @classmethod
    def start(cls, session, stage):
        with _lock:
            session = cls.lookup(session["replay_id"])
            previous = STAGES[:STAGES.index(stage)]
            if any(cls.position(session, s) < 1 for s in previous):
                raise ValueError("Complete previous stages before starting " + stage)
            clock = session["stages"][stage]
            if clock["started_at"] is None:
                clock["started_at"] = time.time()
                session["current_stage"] = stage
            elif clock["paused"]:
                clock.update(started_at=time.time(), paused=False)
            cls.save(session)
            return session

    @classmethod
    def stop(cls, session):
        with _lock:
            session = cls.lookup(session["replay_id"])
            clock = session["stages"][session["current_stage"]]
            if not clock["paused"]:
                clock["elapsed"] = cls.position(session, session["current_stage"]) * session["stage_seconds"]
                clock["paused"] = True
                cls.save(session)
            return session

    @classmethod
    def status(cls, session):
        now = time.time()
        result = copy.deepcopy(session)
        result.pop("mapping", None)
        for stage in STAGES:
            result["stages"][stage]["position"] = cls.position(session, stage, now)
        result["playback_position"] = result["stages"][session["current_stage"]]["position"]
        result["completed"] = result["stages"]["report"]["position"] == 1
        return result


class ReplayView:
    def __init__(self, session):
        self.session = session
        self.folder = ReplayStore.root() / session["replay_id"] / "artifacts"
        if not self.folder.exists():
            raise FileNotFoundError("Replay artifacts are missing")
        now = time.time()
        self.positions = {s: ReplayStore.position(session, s, now) for s in STAGES}
        self.index = EventIndex(self.folder.parent)

    def artifact(self, name):
        return remap(read_json(self.folder / name), self.session["mapping"])

    def visible(self, stage, kind=None, **options):
        # A single captured position is used for all related projections in this request.
        if self.session["stages"][stage]["started_at"] is None:
            return 0 if options.get("count") else []
        rows = self.index.query(stage, kind, self.positions[stage], **options)
        if options.get("count"):
            return rows
        return [remap(row, self.session["mapping"]) for row in rows]

    def latest(self, stage, kind, default=None):
        rows = self.visible(stage, kind, reverse=True, limit=1)
        return rows[-1] if rows else copy.deepcopy(default)

    def meta(self, data):
        if isinstance(data, dict):
            data.update(run_mode="saved", replay_id=self.session["replay_id"])
        return data

    def project(self):
        data = self.artifact("project.json")
        from ..models.project import ProjectManager
        data["token_usage"] = ProjectManager.get_aggregated_usage(self.session["project_id"])
        saved_project = read_json(Path(ProjectManager._get_project_dir(self.session["project_id"])) / "project.json")
        data["files"] = saved_project.get("files", [])
        data.update(record_run=False, graph_build_task_id=self.session["task_ids"]["graph"])
        if self.positions["ontology"] < 1:
            data.update(status="created", ontology=None, analysis_summary=None, graph_id=None)
        elif self.positions["graph"] < 1:
            data["status"] = "graph_building" if self.session["stages"]["graph"]["started_at"] is not None else "ontology_generated"
            data["graph_id"] = self.session["graph_id"] if self.visible("graph", "graph", count=True) else None
        else:
            data.update(status="graph_completed", graph_id=self.session["graph_id"])
        return self.meta(data)

    def graph(self):
        if self.positions["simulation"] == 1:
            return self.meta(self.artifact("graph_final.json"))
        snapshot = self.latest("simulation", "graph")
        if snapshot is not None:
            return self.meta(snapshot)
        data = self.latest("graph", "graph", {"nodes": [], "edges": [], "node_count": 0, "edge_count": 0})
        return self.meta(data)

    def preparation(self):
        if self.positions["preparation"] == 1:
            return self.artifact("prepared.json")
        return self.latest("preparation", "progress", {})

    def simulation(self):
        data = self.artifact("simulation.json")
        prep = self.preparation()
        data.update(prep.get("state.json", {}))
        if self.positions["preparation"] < 1:
            data.update(status="preparing" if self.session["stages"]["preparation"]["started_at"] is not None else "created",
                        config_generated=False, profiles_count=len(prep.get("reddit_profiles.json", [])))
        else:
            data["status"] = "completed" if self.positions["simulation"] == 1 else ("running" if self.session["stages"]["simulation"]["started_at"] is not None else "ready")
        return self.meta(data)

    def task(self, stage):
        data = self.latest(stage, "task", {})
        pos = self.positions[stage]
        started = self.session["stages"][stage]["started_at"] is not None
        data.update(task_id=self.session["task_ids"][stage],
                    status="completed" if pos == 1 else ("processing" if started else "not_started"),
                    progress=100 if pos == 1 else min(99, data.get("progress", int(pos * 100))),
                    message=data.get("message", "Saved simulation playback"),
                    simulation_id=self.session["simulation_id"], report_id=self.session["report_id"])
        if pos < 1:
            data["result"] = None
        elif stage == "graph":
            data["result"] = {"project_id": self.session["project_id"], "graph_id": self.session["graph_id"],
                              "node_count": self.graph().get("node_count", 0), "edge_count": self.graph().get("edge_count", 0)}
        elif stage == "preparation":
            data["result"] = self.simulation()
            data.update(already_prepared=True, prepare_info=self.simulation())
        elif stage == "report":
            data["result"] = {"report_id": self.session["report_id"], "simulation_id": self.session["simulation_id"], "status": "completed"}
        return self.meta(data)

    def actions(self, platform=None, agent_id=None, round_num=None, **options):
        filters = {key: value for key, value in (("platform", platform or None), ("agent_id", agent_id), ("round_num", round_num)) if value is not None}
        return self.visible("simulation", "action", filters=filters, **options)

    def run(self, platform=None):
        data = self.latest("simulation", "state")
        if data is None:
            data = self.artifact("run.json")
        if self.session["stages"]["simulation"]["started_at"] is None:
            data.update(current_round=0, simulated_hours=0, twitter_current_round=0, reddit_current_round=0,
                        twitter_simulated_hours=0, reddit_simulated_hours=0)
        actions = self.actions(platform)
        data.update(all_actions=actions, recent_actions=[a for a in actions if a.get("round_num") == data.get("current_round")],
                    process_pid=None, rounds_count=len({a.get("round_num") for a in actions}))
        started = self.session["stages"]["simulation"]["started_at"] is not None
        paused = self.session["stages"]["simulation"]["paused"]
        data["runner_status"] = "completed" if self.positions["simulation"] == 1 else ("stopped" if paused else ("running" if started else "idle"))
        for p in ("twitter", "reddit"):
            platform_actions = [a for a in actions if a.get("platform") == p]
            data[p + "_actions"] = platform_actions
            data[p + "_actions_count"] = len(platform_actions)
            enabled = self.artifact("simulation.json").get("enable_" + p, True)
            data[p + "_running"] = enabled and started and not paused and self.positions["simulation"] < 1
            data[p + "_completed"] = enabled and self.positions["simulation"] == 1
        data["total_actions_count"] = len(actions)
        data["progress_percent"] = round(data.get("current_round", 0) / max(data.get("total_rounds", 1), 1) * 100, 1)
        return self.meta(data)

    def report(self):
        data = self.artifact("report.json")
        if self.positions["report"] < 1:
            data.update(status="generating" if self.session["stages"]["report"]["started_at"] is not None else "pending",
                        markdown_content="", completed_at=None, outline=self.latest("report", "outline"))
            if data["outline"]:
                for section in data["outline"].get("sections", []):
                    section["content"] = ""
        return self.meta(data)

    def sections(self):
        sections = {}
        for item in self.visible("report", "sections"):
            for section in item:
                sections[section["filename"]] = section
        return list(sections.values())

    def logs(self, kind, **options):
        return self.visible("report", kind, **options)

    def database_rows(self, platform, table, *, offset=0, limit=None, post_id=None, count=False):
        import sqlite3
        if platform not in {"twitter", "reddit"}:
            raise ValueError("platform must be twitter or reddit")
        if table not in {"post", "comment"}:
            raise ValueError("Invalid simulation table")
        if self.session["stages"]["simulation"]["started_at"] is None:
            return 0 if count else []
        path = Path(Config.OASIS_SIMULATION_DATA_DIR) / self.session["simulation_id"] / (platform + "_simulation.db")
        if not path.exists():
            if self.artifact("simulation.json").get("enable_" + platform):
                raise FileNotFoundError("Replay simulation database is missing: " + platform)
            return 0 if count else []
        state = self.latest("simulation", "state")
        if state is None:
            state = self.artifact("run.json")
        cutoff = state.get("database_visibility", {}).get(platform + ":" + table, 0)
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if not exists:
                return 0 if count else []
            where, params = "rowid <= ?", [cutoff]
            if post_id is not None and table == "comment":
                where += " AND CAST(post_id AS TEXT)=?"
                params.append(str(post_id))
            if count:
                return conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {where}', params).fetchone()[0]
            params.extend([-1 if limit is None else limit, offset])
            return [dict(r) for r in conn.execute(f'SELECT * FROM "{table}" WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?', params)]


def dispatch_replay():
    """Flask before_request dispatcher. Unknown replay calls fail closed."""
    from flask import g, jsonify, request, send_file
    if (request.endpoint or "").startswith("recordings."):
        return None
    endpoint = (request.endpoint or "").split(".")[-1]
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify(success=False, error="Request JSON body must be an object"), 400
    identifiers = [v for k, v in {**request.args.to_dict(), **body, **(request.view_args or {})}.items()
                   if k in {"project_id", "graph_id", "simulation_id", "report_id", "task_id", "replay_id"}]
    try:
        sessions = [s for identity in identifiers if (s := ReplayStore.lookup(identity)) is not None]
        if not sessions:
            return None
        if len({s["replay_id"] for s in sessions}) != 1:
            raise ValueError("IDs belong to different replay sessions")
        session = sessions[0]
        g.replay_session = session
        view = ReplayView(session)
        args = request.args

        def ok(data, **extra):
            return jsonify(success=True, data=view.meta(data), run_mode="saved", replay_id=session["replay_id"], **extra)

        forbidden = {"force_regenerate", "max_rounds", "chunk_size", "chunk_overlap", "entity_types", "use_llm_for_profiles", "resume_from_round", "force", "force_restart", "enable_graph_memory_update"}
        if any(key in body and body[key] not in (None, False, "", []) for key in forbidden):
            raise ValueError("Saved simulation outcomes cannot be changed; start a new replay to begin again")

        live_endpoints = {"chat_with_report_agent", "interview_agent", "interview_agents_batch", "interview_all_agents",
                          "get_interview_history", "get_env_status", "close_simulation_env", "translate_report",
                          "export_report", "export_translated_report", "get_project_usage", "get_simulation_usage"}
        if endpoint in live_endpoints:
            if endpoint not in {"get_project_usage", "get_simulation_usage"} and view.positions["report"] < 1:
                raise ValueError("Live interaction is available after saved report playback completes")
            if endpoint in {"get_env_status", "interview_agent", "interview_agents_batch", "interview_all_agents"}:
                from .simulation_runner import SimulationRunner
                SimulationRunner.ensure_interaction_runner(session["simulation_id"])
            return None

        if endpoint == "get_project":
            return ok(view.project())
        if endpoint == "get_graph_data":
            return ok(view.graph())
        if endpoint == "build_graph":
            session = ReplayStore.start(session, "graph")
            return ok({"project_id": session["project_id"], "task_id": session["task_ids"]["graph"], "message": "Saved graph playback started"})
        if endpoint == "get_task":
            stage = next(s for s in STAGES if session["task_ids"][s] == (request.view_args or {})["task_id"])
            return ok(view.task(stage))
        if endpoint == "create_simulation":
            if view.positions["graph"] < 1:
                raise ValueError("Complete graph playback before creating the simulation")
            source = view.artifact("simulation.json")
            if any(k in body and body[k] != source.get(k) for k in ("enable_twitter", "enable_reddit")):
                raise ValueError("Recorded platforms cannot be changed")
            return ok(view.simulation())
        if endpoint == "start_simulation":
            source = view.artifact("simulation.json")
            recorded_platform = "parallel" if source.get("enable_twitter") and source.get("enable_reddit") else ("twitter" if source.get("enable_twitter") else "reddit")
            if body.get("platform", recorded_platform) != recorded_platform:
                raise ValueError("Recorded simulation platforms cannot be changed")
        if endpoint in {"prepare_simulation", "start_simulation", "generate_report", "resume_report"}:
            stage = {"prepare_simulation": "preparation", "start_simulation": "simulation", "generate_report": "report", "resume_report": "report"}[endpoint]
            session = ReplayStore.start(session, stage)
            view = ReplayView(session)
            if stage == "simulation":
                return ok(view.run())
            return ok({"simulation_id": session["simulation_id"], "report_id": session["report_id"],
                       "task_id": session["task_ids"][stage], "status": "completed" if view.positions[stage] == 1 else ("preparing" if stage == "preparation" else "generating"),
                       "already_prepared": view.positions[stage] == 1, "already_generated": view.positions[stage] == 1})
        if endpoint == "stop_simulation":
            return ok(ReplayView(ReplayStore.stop(session)).run())
        if endpoint in {"get_prepare_status", "get_generate_status"}:
            return ok(view.task("preparation" if endpoint == "get_prepare_status" else "report"))
        if endpoint == "get_simulation":
            return ok(view.simulation())
        if endpoint in {"get_simulation_profiles", "get_simulation_profiles_realtime"}:
            platform = args.get("platform", "reddit")
            if platform not in {"reddit", "twitter"}:
                raise ValueError("platform must be twitter or reddit")
            profiles = view.preparation().get("reddit_profiles.json" if platform == "reddit" else "twitter_profiles.csv", [])
            return ok({"simulation_id": session["simulation_id"], "platform": platform, "count": len(profiles), "profiles": profiles,
                       "total_expected": view.simulation().get("entities_count", 0), "is_generating": view.positions["preparation"] < 1,
                       "file_exists": bool(profiles), "file_modified_at": None})
        if endpoint in {"get_simulation_config", "get_simulation_config_realtime", "download_simulation_config"}:
            config = view.preparation().get("simulation_config.json")
            if endpoint == "download_simulation_config":
                if config is None:
                    raise ValueError("Recorded config is not visible yet")
                return send_file(Path(Config.OASIS_SIMULATION_DATA_DIR) / session["simulation_id"] / "simulation_config.json", as_attachment=True)
            if endpoint == "get_simulation_config":
                return ok(config)
            return ok({"simulation_id": session["simulation_id"], "config": config, "file_exists": config is not None,
                       "config_generated": config is not None and view.positions["preparation"] == 1,
                       "is_generating": view.positions["preparation"] < 1,
                       "generation_stage": "completed" if view.positions["preparation"] == 1 else view.task("preparation").get("progress_detail", {}).get("current_stage"),
                       "file_modified_at": None})
        if endpoint in {"get_run_status", "get_run_status_detail"}:
            return ok(view.run(args.get("platform")))
        if endpoint == "get_simulation_actions":
            filters = (args.get("platform"), args.get("agent_id", type=int), args.get("round_num", type=int))
            selected = view.actions(*filters, reverse=True, offset=max(0, args.get("offset", 0, type=int)),
                                    limit=max(0, min(10000, args.get("limit", 100, type=int))))
            return ok({"actions": selected, "count": len(selected), "total": view.actions(*filters, count=True)})
        if endpoint == "get_simulation_timeline":
            timeline = {}
            for action in view.actions():
                num = action.get("round_num", 0)
                if num < args.get("start_round", 0, type=int) or num > args.get("end_round", 1000000000, type=int):
                    continue
                row = timeline.setdefault(num, {"round_num": num, "twitter_actions": 0, "reddit_actions": 0, "total_actions": 0, "active_agents": set(), "action_types": {}, "first_action_time": action.get("timestamp"), "last_action_time": action.get("timestamp")})
                row[action["platform"] + "_actions"] += 1
                row["total_actions"] += 1
                row["active_agents"].add(action["agent_id"])
                row["last_action_time"] = action.get("timestamp")
                row["action_types"][action["action_type"]] = row["action_types"].get(action["action_type"], 0) + 1
            for row in timeline.values():
                row["active_agents_count"] = len(row["active_agents"])
                row["active_agents"] = sorted(row["active_agents"])
            return ok({"rounds_count": len(timeline), "timeline": [timeline[key] for key in sorted(timeline)]})
        if endpoint == "get_agent_stats":
            agents = {}
            for action in view.actions():
                row = agents.setdefault(action["agent_id"], {"agent_id": action["agent_id"], "agent_name": action.get("agent_name", ""), "total_actions": 0, "twitter_actions": 0, "reddit_actions": 0, "action_types": {}, "platforms": {}, "last_action_time": None})
                row["total_actions"] += 1
                row[action["platform"] + "_actions"] += 1
                row["last_action_time"] = action.get("timestamp")
                row["action_types"][action["action_type"]] = row["action_types"].get(action["action_type"], 0) + 1
                row["platforms"][action["platform"]] = row["platforms"].get(action["platform"], 0) + 1
            return ok({"agents_count": len(agents), "stats": sorted(agents.values(), key=lambda a: a["total_actions"], reverse=True)})
        if endpoint in {"get_simulation_posts", "get_simulation_comments"}:
            key = "posts" if endpoint == "get_simulation_posts" else "comments"
            platform = args.get("platform", "reddit") if key == "posts" else "reddit"
            table = "post" if key == "posts" else "comment"
            post_id = args.get("post_id") or None if key == "comments" else None
            selected = view.database_rows(platform, table, post_id=post_id,
                                          offset=max(0, args.get("offset", 0, type=int)),
                                          limit=max(0, min(10000, args.get("limit", 50, type=int))))
            return ok({"platform": platform, key: selected, "count": len(selected),
                       "total": view.database_rows(platform, table, post_id=post_id, count=True)})
        if endpoint in {"get_report", "get_report_by_simulation"}:
            return ok(view.report(), has_report=session["stages"]["report"]["started_at"] is not None)
        if endpoint == "check_report_status":
            started = session["stages"]["report"]["started_at"] is not None
            return ok({"simulation_id": session["simulation_id"], "has_report": started,
                       "report_id": session["report_id"] if started else None, "report_status": view.report()["status"] if started else None,
                       "interview_unlocked": view.positions["report"] == 1})
        if endpoint == "get_report_progress":
            data = view.latest("report", "progress", view.task("report"))
            data["status"] = "completed" if view.positions["report"] == 1 else ("generating" if session["stages"]["report"]["started_at"] is not None else "pending")
            data["progress"] = 100 if view.positions["report"] == 1 else min(99, data.get("progress", 0))
            return ok(data)
        if endpoint in {"get_report_sections", "get_single_section"}:
            rows = view.sections()
            if endpoint == "get_single_section":
                rows = [r for r in rows if r["section_index"] == request.view_args["section_index"] and not r.get("is_subsection")]
                if not rows:
                    return jsonify(success=False, error="Recorded section is not visible yet"), 404
                return ok(rows[0])
            return ok({"report_id": session["report_id"], "sections": rows, "total_sections": len(rows), "is_complete": view.positions["report"] == 1})
        if endpoint in {"get_agent_log", "stream_agent_log", "get_console_log", "stream_console_log"}:
            kind = "agent_log" if "agent" in endpoint else "console_log"
            start = max(0, args.get("from_line", 0, type=int)) if not endpoint.startswith("stream") else 0
            rows = view.logs(kind, offset=start)
            return ok({"logs": rows, "count": len(rows), "total_lines": view.logs(kind, count=True), "from_line": start, "has_more": False})
        if endpoint in {"get_translated_report", "download_report"}:
            if view.positions["report"] < 1:
                raise ValueError("Complete report playback first")
            from .report_agent import ReportManager
            path = Path(ReportManager._get_translated_html_path(session["report_id"]) if endpoint == "get_translated_report" else ReportManager._get_report_markdown_path(session["report_id"]))
            if not path.exists():
                return jsonify(success=False, error="Saved translation is unavailable"), 404
            return ok(path.read_text(encoding="utf-8")) if endpoint == "get_translated_report" else send_file(path, as_attachment=True)
        if endpoint in {"get_graph_entities", "get_entity_detail", "get_entities_by_type"}:
            entities = view.latest("preparation", "entities_filtered", {}).get("entities", [])
            if endpoint == "get_entity_detail":
                entities = [e for e in entities if e.get("uuid") == request.view_args["entity_uuid"]]
                if not entities:
                    return jsonify(success=False, error="Recorded entity is not visible yet"), 404
                return ok(entities[0])
            types = args.get("entity_types", "").split(",") if args.get("entity_types") else []
            if endpoint == "get_entities_by_type":
                types = [request.view_args["entity_type"]]
            if types:
                entities = [e for e in entities if set(e.get("labels", [])) & set(types)]
            return ok({"entities": entities, "count": len(entities), "filtered_count": len(entities), "entity_types": types})
        if endpoint in {"search_graph_tool", "get_graph_statistics_tool"}:
            from .replay_tools import SavedToolsService
            tools = SavedToolsService(session)
            return ok(tools.search_graph(session["graph_id"], body.get("query", ""), limit=body.get("limit", 10)).to_dict() if endpoint == "search_graph_tool" else tools.get_graph_statistics(session["graph_id"]))
        if endpoint in {"list_simulations", "list_reports"}:
            data = [view.simulation()] if endpoint == "list_simulations" else ([view.report()] if session["stages"]["report"]["started_at"] is not None else [])
            return ok(data, count=len(data))
        raise ValueError("This operation is unavailable for saved simulations: " + endpoint)
    except FileNotFoundError as exc:
        return jsonify(success=False, error=str(exc)), 404
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return jsonify(success=False, error=str(exc)), 400


def decorate_replay_response(response):
    from flask import g, request
    session = getattr(g, "replay_session", None)
    endpoint = (request.endpoint or "").split(".")[-1]
    if response.is_json and endpoint in {"list_projects", "list_simulations", "list_reports"}:
        payload = response.get_json()
        if isinstance(payload.get("data"), list):
            projected = []
            for item in payload["data"]:
                identity = item.get({"list_projects": "project_id", "list_simulations": "simulation_id", "list_reports": "report_id"}[endpoint])
                try:
                    replay = ReplayStore.lookup(identity)
                    if replay:
                        view = ReplayView(replay)
                        if endpoint == "list_reports" and replay["stages"]["report"]["started_at"] is None:
                            continue
                        item = {"list_projects": view.project, "list_simulations": view.simulation, "list_reports": view.report}[endpoint]()
                    projected.append(item)
                except (FileNotFoundError, ValueError):
                    continue
            payload.update(data=projected, count=len(projected))
            response.set_data(json.dumps(payload, ensure_ascii=False))
    if session and response.is_json:
        data = response.get_json()
        data.update(run_mode="saved", replay_id=session["replay_id"])
        if isinstance(data.get("data"), dict):
            data["data"].update(run_mode="saved", replay_id=session["replay_id"])
        response.set_data(json.dumps(data, ensure_ascii=False))
        if request.method == "POST" and (request.endpoint or "").split(".")[-1] in {
            "chat_with_report_agent", "interview_agent", "interview_agents_batch", "interview_all_agents"}:
            with _lock:
                path = ReplayStore.root() / session["replay_id"] / "interactions.jsonl"
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"time": time.time(), "endpoint": request.endpoint,
                                             "request": request.get_json(silent=True), "response": data}, ensure_ascii=False) + "\n")
    return response
