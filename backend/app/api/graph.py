"""
图谱相关API路由
采用项目上下文机制，服务端持久化状态
"""
import time
import json
import os
import traceback
import threading
from flask import request, jsonify
from openai import APIError, APITimeoutError, APIConnectionError, AuthenticationError, RateLimitError
import base64

from . import graph_bp
from ..config import Config
from ..services.ontology_generator import OntologyGenerator
from ..services.graph_backend import get_graph_builder
from ..services.text_processor import TextProcessor
from ..utils.file_parser import FileParser
from ..utils.multimodal_processor import MultimodalProcessor
from ..utils.logger import get_logger
from ..utils.llm_client import InvalidLLMResponse, LLMClient
from ..models.task import TaskManager, TaskStatus
from ..models.project import ProjectManager, ProjectStatus

# 获取日志器
logger = get_logger('mirofish.api')


def allowed_file(filename: str) -> bool:
    """检查文件扩展名是否允许"""
    if not filename or '.' not in filename:
        return False
    ext = os.path.splitext(filename)[1].lower().lstrip('.')
    return ext in Config.ALLOWED_EXTENSIONS

# ============== 项目管理接口 ==============

@graph_bp.route('/project/<project_id>', methods=['GET'])
def get_project(project_id: str):
    """
    获取项目详情
    """
    project = ProjectManager.get_project(project_id)
    
    if not project:
        return jsonify({
            "success": False,
            "error": f"项目不存在: {project_id}"
        }), 404
    
    return jsonify({
        "success": True,
        "data": project.to_dict()
    })


@graph_bp.route('/project/list', methods=['GET'])
def list_projects():
    """
    列出所有项目
    """
    limit = request.args.get('limit', 50, type=int)
    projects = ProjectManager.list_projects(limit=limit)
    
    return jsonify({
        "success": True,
        "data": [p.to_dict() for p in projects],
        "count": len(projects)
    })


@graph_bp.route('/project/<project_id>', methods=['DELETE'])
def delete_project(project_id: str):
    """
    删除项目
    """
    success = ProjectManager.delete_project(project_id)
    
    if not success:
        return jsonify({
            "success": False,
            "error": f"项目不存在或删除失败: {project_id}"
        }), 404
    
    return jsonify({
        "success": True,
        "message": f"项目已删除: {project_id}"
    })


@graph_bp.route('/project/<project_id>/reset', methods=['POST'])
def reset_project(project_id: str):
    """
    重置项目状态（用于重新构建图谱）
    """
    project = ProjectManager.get_project(project_id)
    
    if not project:
        return jsonify({
            "success": False,
            "error": f"项目不存在: {project_id}"
        }), 404
    
    # 重置到本体已生成状态
    if project.ontology:
        project.status = ProjectStatus.ONTOLOGY_GENERATED
    else:
        project.status = ProjectStatus.CREATED
    
    project.graph_id = None
    project.graph_build_task_id = None
    project.error = None
    ProjectManager.save_project(project)
    
    return jsonify({
        "success": True,
        "message": f"项目已重置: {project_id}",
        "data": project.to_dict()
    })


@graph_bp.route('/project/<project_id>/usage', methods=['GET'])
def get_project_usage(project_id: str):
    """
    获取项目的 Token 消耗统计 (包含项目级和全链路聚合)
    """
    try:
        # 1. 获取项目的基础消耗 (针对本体生成、图谱生成等非模拟相关的损耗)
        project_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "call_count": 0,
            "last_updated": ""
        }
        usage_file = os.path.join(Config.UPLOAD_FOLDER, 'projects', project_id, 'usage.json')
        if os.path.exists(usage_file):
            with open(usage_file, 'r', encoding='utf-8') as f:
                try:
                    project_usage = json.load(f)
                except: pass
        
        # 1.5 提取特定环节的账单 (如果指定了 step)
        step_id = request.args.get('step')
        display_data = project_usage
        if step_id:
            if "steps" in project_usage and step_id in project_usage["steps"]:
                display_data = project_usage["steps"][step_id]
            else:
                # 如果指定了环节但还没有产生账目，强行清零返回，防止返回总额导致的数值污染
                display_data = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "call_count": 0
                }
        
        # 2. 获取该项目下所有模拟+项目的聚合总计
        total_usage = ProjectManager.get_aggregated_usage(project_id)
        
        return jsonify({
            "success": True,
            "data": display_data,  # 环节数据 (可能为分账数据)
            "total": total_usage    # 项目总计数据
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"获取 Token 用量失败: {str(e)}"
        }), 500


# ============== 接口1：上传文件并生成本体 ==============

@graph_bp.route('/ontology/generate', methods=['POST'])
def generate_ontology():
    """
    接口1：上传文件，分析生成本体定义
    
    请求方式：multipart/form-data
    
    参数：
        files: 上传的文件（PDF/MD/TXT），可多个
        simulation_requirement: 模拟需求描述（必填）
        project_name: 项目名称（可选）
        additional_context: 额外说明（可选）
        
    返回：
        {
            "success": true,
            "data": {
                "project_id": "proj_xxxx",
                "ontology": {
                    "entity_types": [...],
                    "edge_types": [...],
                    "analysis_summary": "..."
                },
                "files": [...],
                "total_text_length": 12345
            }
        }
    """
    project = None
    started = time.monotonic()
    endpoint = LLMClient.sanitized_endpoint(LLMClient._normalize_base_url(Config.EXTRACT_BASE_URL))
    logger.info("Ontology request start model=%s endpoint=%s", Config.EXTRACT_MODEL_NAME, endpoint)
    try:
        logger.info("=== 开始生成本体定义 ===")

        # 获取参数
        simulation_requirement = request.form.get('simulation_requirement', '')
        project_name = request.form.get('project_name', 'Unnamed Project')
        additional_context = request.form.get('additional_context', '')
        
        file_metadata_str = request.form.get('file_metadata', '[]')
        metadata_map = {}
        try:
            file_metadata_list = json.loads(file_metadata_str)
            # 转换为字典，以 filename 为 key，方便后续快速查找描述
            for item in file_metadata_list:
                metadata_map[item.get('name')] = item
        except Exception as e:
            logger.warning(f"解析 file_metadata 失败: {e}")


        if not simulation_requirement:
            return jsonify({
                "success": False,
                "error": "请提供模拟需求描述 (simulation_requirement)"
            }), 400
        
        # 1. DEBUG: Print everything coming from the frontend
        print("--- DEBUG START ---")
        print(f"Files keys: {request.files.keys()}")
        print(f"Form keys: {request.form.keys()}")

        # 获取上传的文件
        uploaded_files = request.files.getlist('files')

        if not uploaded_files or len(uploaded_files) == 0:
            print("Error: No files found in request.files")
            return jsonify({"success": False, "error": "请至少上传一个文档文件"}), 400

        # 3. Parse the metadata string
        metadata_raw = request.form.get('file_metadata', '[]')
        try:
            metadata_list = json.loads(metadata_raw)
            # Create a map for easy lookup
            metadata_map = {m['name']: m for m in metadata_list}
        except Exception as e:
            print(f"Metadata Parse Error: {e}")
            metadata_map = {}

        if not uploaded_files or all(not f.filename for f in uploaded_files):
            return jsonify({
                "success": False,
                "error": "请至少上传一个文档文件"
            }), 400
        
        # 创建项目
        project = ProjectManager.create_project(name=project_name)
        project.recording_inputs = {key: request.form.get(key) for key in ("project_name", "simulation_requirement", "additional_context", "file_metadata")}
        project.record_run = request.form.get("record_run", "false").lower() == "true"
        project.simulation_requirement = simulation_requirement
        ProjectManager.save_project(project)
        image_parser = MultimodalProcessor(project_id=project.project_id)
        logger.info(f"创建项目: {project.project_id}")
        
        # 保存文件并提取文本
        document_texts = []
        all_text = ""
        
        for file in uploaded_files:
            if not file or not file.filename:
                continue
            
            name, ext = os.path.splitext(file.filename)
            ext = ext.lstrip('.')

            # intercept logic to allow images
            if ext in Config.IMAGE_EXTENSIONS:
                file_metadata = metadata_map.get(file.filename, {})
                txt_filename = f"{os.path.basename(name)}_desc.txt"

                logger.info(f"Analyzing {file.filename}...")

                file_bytes = file.read()
                if project.record_run:
                    file.stream.seek(0)
                    original_image = ProjectManager.save_file_to_project(project.project_id, file, file.filename)
                    project.recording_inputs.setdefault("image_files", []).append(original_image)
                base64_image = base64.b64encode(file_bytes).decode('utf-8')

                text = image_parser.describe_image(
                    base64_img=base64_image,
                    short_tag=file_metadata.get('description', '')
                )


                file_info = ProjectManager.save_text_to_project(
                    project.project_id, 
                    text, 
                    txt_filename
                )
            
            elif ext in Config.ALLOWED_EXTENSIONS:
                # 保存文件到项目目录
                file_info = ProjectManager.save_file_to_project(
                    project.project_id, 
                    file, 
                    file.filename
                )
                project.files.append({
                    "filename": file_info["original_filename"],
                    "size": file_info["size"]
                })

                text = FileParser.extract_text(file_info["path"])

            else:
                continue
                
            # 提取文本
            text = TextProcessor.preprocess_text(text)
            document_texts.append(text)
            all_text += f"\n\n=== {file_info['original_filename']} ===\n{text}"
        
        if not document_texts:
            project.status = ProjectStatus.FAILED
            project.error = "No documents could be processed. Check the file formats."
            ProjectManager.save_project(project)
            return jsonify({
                "success": False,
                "error": "没有成功处理任何文档，请检查文件格式"
            }), 400
        
        # 保存提取的文本
        project.total_text_length = len(all_text)
        ProjectManager.save_extracted_text(project.project_id, all_text)
        ProjectManager.save_project(project)
        logger.info(f"文本提取完成，共 {len(all_text)} 字符")
        
        # 生成本体
        logger.info("调用 LLM 生成本体定义...")
        generator = OntologyGenerator(project_id=project.project_id)
        ontology = generator.generate(
            document_texts=document_texts,
            simulation_requirement=simulation_requirement,
            additional_context=additional_context if additional_context else None
        )
        
        # 保存本体到项目
        entity_count = len(ontology.get("entity_types", []))
        edge_count = len(ontology.get("edge_types", []))
        logger.info(f"本体生成完成: {entity_count} 个实体类型, {edge_count} 个关系类型")
        
        project.ontology = {
            "entity_types": ontology.get("entity_types", []),
            "edge_types": ontology.get("edge_types", [])
        }
        project.analysis_summary = ontology.get("analysis_summary", "")
        project.status = ProjectStatus.ONTOLOGY_GENERATED
        ProjectManager.save_project(project)
        logger.info(f"=== 本体生成完成 === 项目ID: {project.project_id}")
        
        return jsonify({
            "success": True,
            "data": {
                "project_id": project.project_id,
                "project_name": project.name,
                "ontology": project.ontology,
                "analysis_summary": project.analysis_summary,
                "files": project.files,
                "total_text_length": project.total_text_length
            }
        })
        
    except Exception as exc:
        status = 500
        if isinstance(exc, APITimeoutError):
            status = 504
            message = "Ontology provider timed out (90-second timeout per call). Check your projects before trying again."
        elif isinstance(exc, AuthenticationError):
            status = 502
            message = "Ontology provider authentication failed. Check the configured API credentials."
        elif isinstance(exc, RateLimitError):
            status = 502
            message = "Ontology provider rate limit or quota exceeded. Check the provider quota before trying again."
        elif isinstance(exc, APIConnectionError):
            status = 502
            message = "Could not connect to the ontology provider. Check the configured endpoint and network connection."
        elif isinstance(exc, APIError):
            status = 502
            upstream_status = getattr(exc, 'status_code', None)
            detail = f" (HTTP {upstream_status})" if upstream_status else ""
            message = f"Ontology provider request failed{detail}. Check the provider configuration and availability."
        elif isinstance(exc, InvalidLLMResponse):
            status = 502
            message = "Ontology provider returned an unusable response. Try again or check the configured model."
        else:
            message = "Ontology generation failed while processing the project. Check the backend configuration and files."

        logger.error("Ontology request failed elapsed=%.2fs model=%s endpoint=%s exception=%s",
                     time.monotonic() - started, Config.EXTRACT_MODEL_NAME, endpoint, type(exc).__name__)
        if project is not None:
            project.status = ProjectStatus.FAILED
            project.error = message
            try:
                ProjectManager.save_project(project)
            except Exception as save_exc:
                logger.error("Could not persist failed project exception=%s", type(save_exc).__name__)
        return jsonify({
            "success": False,
            "data": {"project_id": project.project_id} if project else None,
            "error": message
        }), status
    finally:
        logger.info("Ontology request finished elapsed=%.2fs model=%s endpoint=%s",
                    time.monotonic() - started, Config.EXTRACT_MODEL_NAME, endpoint)


# ============== 接口2：构建图谱 ==============

@graph_bp.route('/build', methods=['POST'])
def build_graph():
    """
    接口2：根据project_id构建图谱
    
    请求（JSON）：
        {
            "project_id": "proj_xxxx",  // 必填，来自接口1
            "graph_name": "图谱名称",    // 可选
            "chunk_size": 500,          // 可选，默认500
            "chunk_overlap": 50         // 可选，默认50
        }
        
    返回：
        {
            "success": true,
            "data": {
                "project_id": "proj_xxxx",
                "task_id": "task_xxxx",
                "message": "图谱构建任务已启动"
            }
        }
    """
    try:
        logger.info("=== 开始构建图谱 ===")
        
        # 检查配置（不同后端有不同要求）
        errors = Config.validate()
        if errors:
            logger.error(f"配置错误: {errors}")
            return jsonify({"success": False, "error": "配置错误: " + "; ".join(errors)}), 500
        
        # 解析请求
        data = request.get_json() or {}
        project_id = data.get('project_id')
        logger.debug(f"请求参数: project_id={project_id}")
        
        if not project_id:
            return jsonify({
                "success": False,
                "error": "请提供 project_id"
            }), 400
        
        # 获取项目
        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"项目不存在: {project_id}"
            }), 404
        
        # 检查项目状态
        force = data.get('force', False)  # 强制重新构建
        
        if project.status == ProjectStatus.CREATED:
            return jsonify({
                "success": False,
                "error": "项目尚未生成本体，请先调用 /ontology/generate"
            }), 400
        
        if project.status == ProjectStatus.GRAPH_BUILDING and not force:
            return jsonify({
                "success": False,
                "error": "图谱正在构建中，请勿重复提交。如需强制重建，请添加 force: true",
                "task_id": project.graph_build_task_id
            }), 400
        
        # 如果强制重建，重置状态
        if force and project.status in [ProjectStatus.GRAPH_BUILDING, ProjectStatus.FAILED, ProjectStatus.GRAPH_COMPLETED]:
            project.status = ProjectStatus.ONTOLOGY_GENERATED
            project.graph_id = None
            project.graph_build_task_id = None
            project.error = None
        
        # 获取配置
        graph_name = data.get('graph_name', project.name or 'MiroFish Graph')
        chunk_size = data.get('chunk_size', project.chunk_size or Config.DEFAULT_CHUNK_SIZE)
        chunk_overlap = data.get('chunk_overlap', project.chunk_overlap or Config.DEFAULT_CHUNK_OVERLAP)
        
        # 增加防御性参数校验，防止死循环
        if chunk_size <= chunk_overlap:
            return jsonify({
                "success": False,
                "error": f"非法参数配置: 分片大小 ({chunk_size}) 必须大于重叠大小 ({chunk_overlap})"
            }), 400
            
        # 更新项目配置
        project.chunk_size = chunk_size
        project.chunk_overlap = chunk_overlap
        
        # 获取提取的文本
        text = ProjectManager.get_extracted_text(project_id)
        if not text:
            return jsonify({
                "success": False,
                "error": "未找到提取的文本内容"
            }), 400
        
        # 获取本体
        ontology = project.ontology
        if not ontology:
            return jsonify({
                "success": False,
                "error": "未找到本体定义"
            }), 400
        
        # 创建异步任务
        task_manager = TaskManager()
        task_id = task_manager.create_task(f"构建图谱: {graph_name}", metadata={"project_id": project_id, "recording_stage": "graph"})
        logger.info(f"创建图谱构建任务: task_id={task_id}, project_id={project_id}")
        
        # 更新项目状态
        project.status = ProjectStatus.GRAPH_BUILDING
        project.graph_build_task_id = task_id
        ProjectManager.save_project(project)
        
        # 启动后台任务
        def build_task():
            build_logger = get_logger('mirofish.build')
            try:
                build_logger.info(f"[{task_id}] 开始构建图谱...")
                task_manager.update_task(
                    task_id, 
                    status=TaskStatus.PROCESSING,
                    message="初始化图谱构建服务..."
                )
                
                builder = get_graph_builder()

                if Config.GRAPH_BACKEND == "local":
                    # 本地化：Neo4j +（可选）Qdrant。分块与抽取在 builder 内部完成。
                    def local_progress_callback(msg: str, ratio: float):
                        progress = min(99, max(1, int(ratio * 95)))
                        task_manager.update_task(task_id, message=msg, progress=progress)

                    graph_id, graph_data = builder.build_graph_from_text(
                        project_id=project_id,
                        text=text,
                        ontology=ontology,
                        graph_name=graph_name,
                        chunk_size=chunk_size,
                        chunk_overlap=chunk_overlap,
                        progress_callback=local_progress_callback,
                    )
                    total_chunks = len(TextProcessor.split_text(text, chunk_size=chunk_size, overlap=chunk_overlap))
                else:
                    # Zep Cloud：保持原有流程
                    task_manager.update_task(task_id, message="文本分块中...", progress=5)
                    chunks = TextProcessor.split_text(text, chunk_size=chunk_size, overlap=chunk_overlap)
                    total_chunks = len(chunks)

                    task_manager.update_task(task_id, message="创建Zep图谱...", progress=10)
                    graph_id = builder.create_graph(name=graph_name)

                    task_manager.update_task(task_id, message="设置本体定义...", progress=15)
                    builder.set_ontology(graph_id, ontology)

                    def add_progress_callback(msg, progress_ratio):
                        progress = 15 + int(progress_ratio * 40)  # 15% - 55%
                        task_manager.update_task(task_id, message=msg, progress=progress)

                    task_manager.update_task(task_id, message=f"开始添加 {total_chunks} 个文本块...", progress=15)
                    episode_uuids = builder.add_text_batches(
                        graph_id,
                        chunks,
                        batch_size=3,
                        progress_callback=add_progress_callback,
                    )

                    task_manager.update_task(task_id, message="等待Zep处理数据...", progress=55)

                    def wait_progress_callback(msg, progress_ratio):
                        from ..services.recording import RecordingStore
                        if project.record_run:
                            try:
                                RecordingStore.graph(project_id, builder.get_graph_data(graph_id))
                            except Exception as capture_error:
                                RecordingStore.failure(project_id, str(capture_error))
                        progress = 55 + int(progress_ratio * 35)  # 55% - 90%
                        task_manager.update_task(task_id, message=msg, progress=progress)

                    builder._wait_for_episodes(episode_uuids, wait_progress_callback)

                    task_manager.update_task(task_id, message="获取图谱数据...", progress=95)
                    graph_data = builder.get_graph_data(graph_id)

                # 更新项目的graph_id
                from ..services.recording import RecordingStore
                RecordingStore.graph(project_id, graph_data)
                project.graph_id = graph_id
                ProjectManager.save_project(project)
                
                # 更新项目状态
                project.status = ProjectStatus.GRAPH_COMPLETED
                ProjectManager.save_project(project)
                
                node_count = graph_data.get("node_count", 0)
                edge_count = graph_data.get("edge_count", 0)
                build_logger.info(f"[{task_id}] 图谱构建完成: graph_id={graph_id}, 节点={node_count}, 边={edge_count}")
                
                # 完成
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.COMPLETED,
                    message="图谱构建完成",
                    progress=100,
                    result={
                        "project_id": project_id,
                        "graph_id": graph_id,
                        "node_count": node_count,
                        "edge_count": edge_count,
                        "chunk_count": total_chunks
                    }
                )
                
            except Exception as e:
                # 更新项目状态为失败
                build_logger.error(f"[{task_id}] 图谱构建失败: {str(e)}")
                build_logger.debug(traceback.format_exc())
                
                project.status = ProjectStatus.FAILED
                project.error = str(e)
                ProjectManager.save_project(project)
                
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.FAILED,
                    message=f"构建失败: {str(e)}",
                    error=traceback.format_exc()
                )
        
        # 启动后台线程
        thread = threading.Thread(target=build_task, daemon=True)
        thread.start()
        
        return jsonify({
            "success": True,
            "data": {
                "project_id": project_id,
                "task_id": task_id,
                "message": "图谱构建任务已启动，请通过 /task/{task_id} 查询进度"
            }
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 任务查询接口 ==============

@graph_bp.route('/task/<task_id>', methods=['GET'])
def get_task(task_id: str):
    """
    查询任务状态
    """
    task = TaskManager().get_task(task_id)
    
    if not task:
        return jsonify({
            "success": False,
            "error": f"任务不存在: {task_id}"
        }), 404
    
    return jsonify({
        "success": True,
        "data": task.to_dict()
    })


@graph_bp.route('/tasks', methods=['GET'])
def list_tasks():
    """
    列出所有任务
    """
    tasks = TaskManager().list_tasks()
    
    return jsonify({
        "success": True,
        "data": [t.to_dict() for t in tasks],
        "count": len(tasks)
    })


# ============== 图谱数据接口 ==============

@graph_bp.route('/data/<graph_id>', methods=['GET'])
def get_graph_data(graph_id: str):
    """
    获取图谱数据（节点和边）
    """
    try:
        errors = Config.validate()
        if errors:
            return jsonify({"success": False, "error": "配置错误: " + "; ".join(errors)}), 500

        builder = get_graph_builder()
        graph_data = builder.get_graph_data(graph_id)
        
        return jsonify({
            "success": True,
            "data": graph_data
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@graph_bp.route('/delete/<graph_id>', methods=['DELETE'])
def delete_graph(graph_id: str):
    """
    删除Zep图谱
    """
    try:
        errors = Config.validate()
        if errors:
            return jsonify({"success": False, "error": "配置错误: " + "; ".join(errors)}), 500

        builder = get_graph_builder()
        builder.delete_graph(graph_id)
        
        return jsonify({
            "success": True,
            "message": f"图谱已删除: {graph_id}"
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500
