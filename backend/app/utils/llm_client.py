import json
import re
import logging
import os
import fcntl
import time
from urllib.parse import urlsplit, urlunsplit
from typing import Optional, Dict, Any, List
from datetime import datetime
from openai import OpenAI, APIStatusError
from json_repair import repair_json

from ..config import Config

logger = logging.getLogger('mirofish.llm_client')


class InvalidLLMResponse(ValueError):
    """The provider returned content that cannot be used as a JSON object."""


class LLMClient:
    """LLM客户端 - 支持全链路物理对齐与环节分账"""

    # 全局 Token 用量累计（内存级参考，主由于单次会话审计）
    _cumulative_tokens = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "call_count": 0
    }

    @classmethod
    def get_token_stats(cls) -> Dict[str, int]:
        """获取累计 token 用量统计"""
        return dict(cls._cumulative_tokens)

    @classmethod
    def reset_token_stats(cls):
        """重置 token 统计"""
        cls._cumulative_tokens = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "call_count": 0
        }

    def __init__(
        self, 
        api_key: Optional[str] = None, 
        base_url: Optional[str] = None, 
        model: Optional[str] = None,
        simulation_id: Optional[str] = None,
        project_id: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None
    ):
        """
        初始化LLM客户端

        Args:
            api_key: API Key
            base_url: Base URL
            model: 模型名称
            simulation_id: 模拟ID
            project_id: 项目ID
            timeout: Optional SDK timeout per call; omitted to use SDK defaults.
            max_retries: Optional SDK retries; omitted to use SDK defaults.
        """
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = self._normalize_base_url(base_url or Config.LLM_BASE_URL)
        self.model = model or Config.LLM_MODEL_NAME
        self.simulation_id = simulation_id or os.environ.get('SIMULATION_ID')
        self.project_id = project_id or os.environ.get('LLM_PROJECT_ID') or os.environ.get('PROJECT_ID')
        self.step = os.environ.get('LLM_STEP_NAME')  # 从环境读取默认环节标识 (step1-step5)
        
        client_options = {}
        if timeout is not None:
            client_options['timeout'] = timeout
        if max_retries is not None:
            client_options['max_retries'] = max_retries
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            **client_options
        )
        # Embeddings client (使用配置中的 Embedding 专项配置)
        self._embedding_client = OpenAI(
            api_key=Config.EMBEDDING_API_KEY,
            base_url=self._normalize_base_url(Config.EMBEDDING_BASE_URL),
            **client_options
        )

    def _log_token_usage(self, response, caller_hint: str = "", simulation_id: Optional[str] = None, project_id: Optional[str] = None, step: Optional[str] = None):
        """
        记录 token 用量，并输出物理对齐日志。
        此方法的日志输出直接与磁盘上的 usage.json 挂钩，确保前后端永久对齐。
        """
        usage = getattr(response, 'usage', None)
        if not usage:
            return
        prompt_tokens = getattr(usage, 'prompt_tokens', 0) or 0
        completion_tokens = getattr(usage, 'completion_tokens', 0) or 0
        total_tokens = getattr(usage, 'total_tokens', 0) or (prompt_tokens + completion_tokens)
        
        # 优先级：参数 > 实例属性
        sid = simulation_id or getattr(self, 'simulation_id', None)
        pid = project_id or getattr(self, 'project_id', None)
        s_name = step or getattr(self, 'step', None)
        
        # 核心：登记并获取磁盘物理快照
        results = self.register_token_usage(prompt_tokens, completion_tokens, caller_hint, sid, pid, s_name)
        
        # 输出与前端绝对对齐的物理日志
        # 语义精准化标签
        scope_label = "当前仿真累计" if sid else "项目环节全量"
        step_str = f"环节[{s_name}]: {results['step'].get('total_tokens', 0)} | " if s_name else ""
        sim_total = results['simulation']['total_tokens']
        proj_total = results['project']['total_tokens']
        call_num = results['simulation']['call_count']
        
        logger.info(
            f"🔢 Token 物理对齐 [{caller_hint}]: prompt={prompt_tokens}, completion={completion_tokens}, total={total_tokens} | "
            f"{step_str}{scope_label}: {sim_total} | 项目总累计: {proj_total} | 第{call_num}次调用"
        )

    @classmethod
    def register_token_usage(
        cls, 
        prompt_tokens: int, 
        completion_tokens: int, 
        caller_hint: str = "", 
        simulation_id: Optional[str] = None,
        project_id: Optional[str] = None,
        step: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        登记 token 用量并回传物理快照。
        
        Returns:
            Dict: 包含 step(环节累计), simulation(仿真累计), project(项目聚合) 的物理快照
        """
        total_tokens = prompt_tokens + completion_tokens
        current_step = step or os.environ.get('LLM_STEP_NAME')
        
        # 1. 更新内存累计 (基础参考，重启重置)
        cls._cumulative_tokens["prompt_tokens"] += prompt_tokens
        cls._cumulative_tokens["completion_tokens"] += completion_tokens
        cls._cumulative_tokens["total_tokens"] += total_tokens
        cls._cumulative_tokens["call_count"] += 1
        
        # 2. 持久化到磁盘并计算物理总量
        target_id = simulation_id or project_id
        usage_data = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "call_count": 0}
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "call_count": 0}
        
        if target_id:
            try:
                # 确定存储主路径 (优先仿真目录)
                if simulation_id:
                    target_dir = os.path.join(Config.UPLOAD_FOLDER, 'simulations', simulation_id)
                else:
                    # 恢复：项目路径回归标准目录
                    target_dir = os.path.join(Config.UPLOAD_FOLDER, 'projects', project_id)
                
                os.makedirs(target_dir, exist_ok=True)
                usage_file = os.path.join(target_dir, 'usage.json')
                
                # 使用文件锁保证原子性
                # a+ 模式打开以防文件不存在，但读取时需要 seek(0)
                with open(usage_file, 'a+') as f_lock:
                    fcntl.flock(f_lock, fcntl.LOCK_EX) # 阻塞直到获得排他锁
                    try:
                        # 读取
                        f_lock.seek(0)
                        content = f_lock.read()
                        usage_data = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "call_count": 0, "steps": {}}
                        if content:
                            try: usage_data = json.loads(content)
                            except: pass
                        
                        # 累加
                        usage_data["prompt_tokens"] += prompt_tokens
                        usage_data["completion_tokens"] += completion_tokens
                        usage_data["total_tokens"] += total_tokens
                        usage_data["call_count"] = usage_data.get("call_count", 0) + 1
                        usage_data["last_updated"] = datetime.now().isoformat()
                        
                        # 环节分账
                        if current_step:
                            if "steps" not in usage_data: usage_data["steps"] = {}
                            if current_step not in usage_data["steps"]:
                                usage_data["steps"][current_step] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "call_count": 0}
                            
                            s = usage_data["steps"][current_step]
                            s["prompt_tokens"] += prompt_tokens
                            s["completion_tokens"] += completion_tokens
                            s["total_tokens"] += total_tokens
                            s["call_count"] += 1
                        
                        # 原子写回 (清空并重写)
                        f_lock.seek(0)
                        f_lock.truncate()
                        json.dump(usage_data, f_lock, indent=2)
                        f_lock.flush()
                        os.fsync(f_lock.fileno())
                    finally:
                        fcntl.flock(f_lock, fcntl.LOCK_UN) # 释放

                # 获取项目级全链路聚合 (物理穿透)
                # 修改为绝对导入，以兼容子进程独立运行模式
                from app.models.project import ProjectManager
                pid = project_id
                if not pid and simulation_id:
                    from app.services.simulation_manager import SimulationManager
                    state = SimulationManager().get_simulation(simulation_id)
                    if state: pid = state.project_id
                
                if pid:
                    total_usage = ProjectManager.get_aggregated_usage(pid)
                else:
                    total_usage = usage_data
            except Exception as e:
                logger.warning(f"计费物理快照抓取失败 ({target_id}): {e}")

        # 返回物理快照
        return {
            "step": usage_data.get("steps", {}).get(current_step, {}) if current_step else {},
            "simulation": usage_data,
            "project": total_usage
        }

    @staticmethod
    def _normalize_base_url(url: str) -> str:
        """规范化 OpenAI API 基础路径"""
        if not url:
            return url
        parts = urlsplit(url.strip())
        path = parts.path.rstrip('/') or '/v1'
        return urlunsplit(parts._replace(path=path))

    @staticmethod
    def sanitized_endpoint(url: str) -> str:
        """Exclude URL credentials, query parameters and fragments from logs."""
        parts = urlsplit(url)
        host = parts.hostname or ''
        if ':' in host:
            host = f'[{host}]'
        if parts.port:
            host += f':{parts.port}'
        return urlunsplit((parts.scheme, host, parts.path, '', ''))

    @staticmethod
    def _extract_json_object(text: str) -> Optional[str]:
        """从模型回复中提取 JSON 字符串 - 增强版支持修复部分截断"""
        if not text:
            return None
        
        # 1. 预清洗：移除 Markdown 栅栏
        clean_text = re.sub(r"```json\s*", "", text)
        clean_text = re.sub(r"```\s*", "", clean_text).strip()
        
        # 2. 查找起始点 (支持对象 { 或 数组 [)
        start_brace = clean_text.find("{")
        start_bracket = clean_text.find("[")
        
        # 确定真正的起始位置
        if start_brace == -1 and start_bracket == -1:
            return None
        
        if start_brace != -1 and (start_bracket == -1 or start_brace < start_bracket):
            start_idx = start_brace
        else:
            start_idx = start_bracket

        # 3. 关键改动：不再寻找 end_brace。
        # 直接截取从 start 到结尾的所有内容。
        # 这样 json_repair 才能接收到 {"entities": [{"name": 并将其修复为 {"entities": [{"name": ""}]}
        return clean_text[start_idx:].strip()

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None,
        caller_hint: str = "chat",
        simulation_id: Optional[str] = None,
        project_id: Optional[str] = None
    ) -> str:
        """发送聊天请求"""
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            kwargs["response_format"] = response_format
        
        started = time.monotonic()
        endpoint = self.sanitized_endpoint(self.base_url)
        logger.info("LLM request start model=%s endpoint=%s", self.model, endpoint)
        try:
            response = self.client.chat.completions.create(**kwargs)
            choices = getattr(response, 'choices', None)
            content = getattr(getattr(choices[0], 'message', None), 'content', None) if choices else None
            if not isinstance(content, str):
                raise InvalidLLMResponse('Provider returned no text response.')
        except Exception as exc:
            logger.warning("LLM request failed elapsed=%.2fs model=%s endpoint=%s exception=%s",
                           time.monotonic() - started, self.model, endpoint, type(exc).__name__)
            raise
        logger.info("LLM request complete elapsed=%.2fs model=%s endpoint=%s",
                    time.monotonic() - started, self.model, endpoint)
        # 统一侧重物理快照记录
        sid = simulation_id or getattr(self, 'simulation_id', None)
        pid = project_id or getattr(self, 'project_id', None)
        self._log_token_usage(response, caller_hint, sid, pid)
        return content

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 32000,
        caller_hint: str = "chat_json",
        simulation_id: Optional[str] = None,
        project_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Request JSON with at most one fallback for output/format problems."""
        try:
            response = self.chat(
                messages=messages, temperature=temperature, max_tokens=max_tokens,
                response_format={"type": "json_object"}, caller_hint=caller_hint,
                simulation_id=simulation_id, project_id=project_id
            )
        except APIStatusError as exc:
            if not self._unsupported_json_format(exc):
                raise
        except InvalidLLMResponse:
            pass
        else:
            try:
                return self._parse_json_response(response)
            except InvalidLLMResponse:
                pass

        logger.info("LLM JSON fallback: invalid output or unsupported JSON format")
        fallback_messages = [
            {"role": "system", "content": "You are a specialized JSON generator. Output ONLY the JSON object. "
             "Ensure all strings are properly escaped. No markdown, no prose."},
            *messages,
        ]
        response = self.chat(
            messages=fallback_messages, temperature=0.0, max_tokens=max_tokens,
            caller_hint=f"{caller_hint}_fallback", simulation_id=simulation_id,
            project_id=project_id
        )
        return self._parse_json_response(response)

    @staticmethod
    def _unsupported_json_format(exc: APIStatusError) -> bool:
        # Generic 400s, moderation rejections and unrelated unsupported options
        # must not cause another billable request.
        if exc.status_code not in (400, 422):
            return False
        body = exc.body if isinstance(exc.body, dict) else {}
        error = body.get('error', body)
        if not isinstance(error, dict):
            return False
        message = str(error.get('message', '')).lower()
        mentions_format = (error.get('param') == 'response_format' or
                           any(term in message for term in ('response_format', 'json_object', 'json mode')))
        rejects_format = any(term in message for term in ('not supported', 'unsupported', 'does not support', 'not available'))
        return mentions_format and rejects_format

    @classmethod
    def _parse_json_response(cls, response: str) -> Dict[str, Any]:
        maybe = cls._extract_json_object(response)
        if not maybe:
            raise InvalidLLMResponse('Provider returned no JSON object.')
        try:
            parsed = json.loads(repair_json(maybe))
        except (ValueError, TypeError, IndexError, RecursionError) as exc:
            raise InvalidLLMResponse('Provider returned invalid JSON.') from exc
        if not isinstance(parsed, dict):
            raise InvalidLLMResponse('Provider returned JSON that is not an object.')
        # Preserve the existing cleanup used by graph extraction workflows.
        for key in ('entities', 'relationships'):
            if isinstance(parsed.get(key), list):
                parsed[key] = [item for item in parsed[key] if isinstance(item, dict)]
            elif isinstance(parsed.get(key), dict):
                parsed[key] = list(parsed[key].values())
        return parsed

    def embed_texts(self, texts: List[str], model: Optional[str] = None) -> List[List[float]]:
        """生成 embeddings"""
        resp = self._embedding_client.embeddings.create(
            model=model or Config.EMBEDDING_MODEL_NAME,
            input=texts,
        )
        return [d.embedding for d in resp.data]
