"""Recording catalog and replay session lifecycle."""
from flask import Blueprint, current_app, jsonify, request

from ..services.recording import RecordingStore
from ..services.replay import ReplayStore

recordings_bp = Blueprint("recordings", __name__)


@recordings_bp.get("/recordings")
def list_recordings():
    return jsonify(success=True, data=RecordingStore.list())


@recordings_bp.post("/replays")
def create_replay():
    try:
        data = request.get_json(silent=True) or {}
        return jsonify(success=True, data=ReplayStore.create(
            data.get("recording_id"), current_app.config["SAVED_SIMULATION_STAGE_SECONDS"]))
    except FileNotFoundError as exc:
        return jsonify(success=False, error=str(exc)), 404
    except (ValueError, TypeError) as exc:
        return jsonify(success=False, error=str(exc)), 400


@recordings_bp.get("/replays/<replay_id>")
def get_replay(replay_id):
    try:
        session = ReplayStore.lookup(replay_id)
        if session is None:
            raise FileNotFoundError("Replay session not found")
        return jsonify(success=True, data=ReplayStore.status(session))
    except FileNotFoundError as exc:
        return jsonify(success=False, error=str(exc)), 404
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400


@recordings_bp.post("/replays/<replay_id>/stop")
def stop_replay(replay_id):
    try:
        session = ReplayStore.lookup(replay_id)
        if session is None:
            raise FileNotFoundError("Replay session not found")
        return jsonify(success=True, data=ReplayStore.status(ReplayStore.stop(session)))
    except (ValueError, FileNotFoundError) as exc:
        return jsonify(success=False, error=str(exc)), 400


@recordings_bp.post("/replays/<replay_id>/resume")
def resume_replay(replay_id):
    try:
        session = ReplayStore.lookup(replay_id)
        if session is None:
            raise FileNotFoundError("Replay session not found")
        return jsonify(success=True, data=ReplayStore.status(ReplayStore.start(session, session["current_stage"])))
    except (ValueError, FileNotFoundError) as exc:
        return jsonify(success=False, error=str(exc)), 400
