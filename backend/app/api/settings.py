import os
import json
import traceback
from flask import request, jsonify, send_file

from . import settings_bp
from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.settings')


@settings_bp.route('/prompts/<stage>', methods=['GET'])
def get_stage_prompts(stage):
    stage_path = os.path.join(Config.PROMPTS_FOLDER, stage)
    if not os.path.exists(stage_path):
        return jsonify({
            "success": False,
            "error": f"Unable to fetch stage prompt: {stage_path}"
        }), 404
    
    prompts = {}
    for filename in os.listdir(stage_path):
        if filename.endswith('.txt'):
            with open(os.path.join(stage_path, filename), 'r', encoding='utf-8') as f:
                prompts[filename] = f.read()

    return jsonify({
        "success": True,
        "data": prompts
    })

@settings_bp.route('/prompts/save', methods=['POST'])
def save_stage_prompt():
    data = request.get_json() or {}

    try:
        stage_path = os.path.join(Config.PROMPTS_FOLDER, data.get("stage"))
        file_path = os.path.join(stage_path, data.get("filename"))

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(data.get("prompt"))

        return jsonify({
            "success": True
        })

    except Exception as e:
        logger.exception(f"Failed to save stage prompt: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@settings_bp.route('/prompts/stages', methods=['GET'])
def get_prompt_stages():
    stages = [folder for folder in os.listdir(Config.PROMPTS_FOLDER)]
    
    return jsonify({
        "success": True,
        "data": stages
    })

# @settings_bp.route('/prompts', methods=['GET'])
# def get_all_prompts():
#     stage_path = os.path.join(Config.PROMPTS_FOLDER)
    
#     all_prompts = {}
#     for folder in os.listdir(stage_path):
#         folder_path = os.path.join(stage_path, folder)
#         stage_prompts = {}

#         for filename in os.listdir(folder_path):
#             if filename.endswith('.txt'):
#                 with open(os.path.join(stage_path, filename), 'r', encoding='utf-8') as f:
#                     stage_prompts[filename] = f.read()

#         all_prompts[folder] = stage_prompts

#     return jsonify(all_prompts)
