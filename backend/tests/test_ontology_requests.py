"""Exercise the real SDK and Flask route using an offline HTTP transport."""
import io
import json
import logging
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
import openai

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Config
from app.models.project import ProjectManager, ProjectStatus
from app.services.ontology_generator import OntologyGenerator
from app.utils.llm_client import InvalidLLMResponse, LLMClient


ONTOLOGY = {"entity_types": [], "edge_types": [], "analysis_summary": "Test analysis"}


def completion(content):
    return httpx.Response(200, json={
        "id": "test", "object": "chat.completion", "created": 0, "model": "test",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}]
    })


class OntologyRequestTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for owner, key, value in (
            (Config, "UPLOAD_FOLDER", str(self.root)),
            (ProjectManager, "PROJECTS_DIR", str(self.root / "projects")),
            (Config, "LLM_API_KEY", "test-key"),
            (Config, "EXTRACT_API_KEY", "test-key"),
            (Config, "EMBEDDING_API_KEY", "test-key"),
            (Config, "EXTRACT_BASE_URL", "https://provider.test/v1beta/openai/"),
            (Config, "DEBUG", False),
        ):
            p = patch.object(owner, key, value)
            p.start()
            self.addCleanup(p.stop)
        self.requests = []

    def use_responses(self, *responses):
        outcomes = iter(responses)

        def handle(request):
            self.requests.append(request)
            result = next(outcomes)
            if isinstance(result, Exception):
                raise result
            return result

        def client(**kwargs):
            result = openai.OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
            self.addCleanup(result.close)
            return result

        p = patch("app.utils.llm_client.OpenAI", side_effect=client)
        p.start()
        self.addCleanup(p.stop)

    def test_normalization_preserves_api_paths_for_chat_and_embeddings(self):
        for url, expected in (
            (" https://generativelanguage.googleapis.com/v1beta/openai/ ", "https://generativelanguage.googleapis.com/v1beta/openai"),
            ("https://api.openai.com/v1", "https://api.openai.com/v1"),
            ("http://localhost:5099/", "http://localhost:5099/v1"),
            ("http://localhost:5099", "http://localhost:5099/v1"),
            ("https://gateway.test/tenant/custom/api/", "https://gateway.test/tenant/custom/api"),
        ):
            with self.subTest(url=url), patch.object(Config, "EMBEDDING_BASE_URL", url):
                client = LLMClient(api_key="test-key", base_url=url)
                self.addCleanup(client.client.close)
                self.addCleanup(client._embedding_client.close)
                self.assertEqual(client.base_url, expected)
                self.assertEqual(str(client._embedding_client.base_url), expected + '/')

    def test_ontology_limits_and_other_workflow_defaults(self):
        generator = OntologyGenerator()
        for client in (generator.llm_client.client, generator.llm_client._embedding_client):
            self.addCleanup(client.close)
            self.assertEqual(client.timeout, 90.0)
            self.assertEqual(client.max_retries, 0)
        client = LLMClient()
        default = openai.OpenAI(api_key="test-key")
        for sdk_client in (client.client, client._embedding_client, default):
            self.addCleanup(sdk_client.close)
        self.assertEqual(client.client.timeout, default.timeout)
        self.assertEqual(client.client.max_retries, default.max_retries)

    def test_transport_and_provider_errors_are_preserved_without_fallback(self):
        request = httpx.Request("POST", "https://provider.test")
        for outcome, error_type in (
            (httpx.ReadTimeout("private timeout detail", request=request), openai.APITimeoutError),
            (httpx.ConnectError("private connection detail", request=request), openai.APIConnectionError),
            (httpx.Response(401, json={"error": {"message": "private auth detail"}}), openai.AuthenticationError),
            (httpx.Response(429, json={"error": {"message": "private quota detail"}}), openai.RateLimitError),
            (httpx.Response(503, json={"error": {"message": "private provider detail"}}), openai.InternalServerError),
            (httpx.Response(400, json={"error": {"message": "unsupported temperature"}}), openai.BadRequestError),
        ):
            with self.subTest(error=error_type.__name__):
                self.requests.clear()
                self.use_responses(outcome)
                llm = OntologyGenerator().llm_client
                with self.assertRaises(error_type):
                    llm.chat_json(messages=[{"role": "user", "content": "test"}])
                self.assertEqual(len(self.requests), 1)
                self.assertEqual(self.requests[0].url.path, "/v1beta/openai/chat/completions")
                self.assertTrue(all(value == 90.0 for value in self.requests[0].extensions['timeout'].values()))

    def test_original_exception_instance_propagates(self):
        llm = object.__new__(LLMClient)
        original = openai.APITimeoutError(request=httpx.Request("POST", "https://provider.test"))
        with patch.object(llm, "chat", side_effect=original) as chat:
            with self.assertRaises(openai.APITimeoutError) as caught:
                llm.chat_json(messages=[])
            self.assertIs(caught.exception, original)
            self.assertEqual(chat.call_count, 1)

    def test_invalid_json_and_explicit_unsupported_format_allow_one_fallback(self):
        for first in (
            completion("No JSON here"), completion("[]"), completion(None),
            httpx.Response(200, json={"choices": []}),
            httpx.Response(400, json={"error": {"message": "response_format json_object is not supported"}}),
            httpx.Response(422, json={"error": {"param": "response_format", "message": "Not supported by this model"}}),
        ):
            with self.subTest(first=first):
                self.requests.clear()
                self.use_responses(first, completion(json.dumps(ONTOLOGY)))
                self.assertEqual(OntologyGenerator().llm_client.chat_json(messages=[]), ONTOLOGY)
                self.assertEqual(len(self.requests), 2)
                self.assertIn("response_format", json.loads(self.requests[0].content))
                self.assertNotIn("response_format", json.loads(self.requests[1].content))

    def test_invalid_fallback_does_not_retry_or_expose_output(self):
        self.use_responses(completion("private output"), completion("private output"))
        with self.assertRaises(InvalidLLMResponse) as caught:
            OntologyGenerator().llm_client.chat_json(messages=[])
        self.assertNotIn("private output", str(caught.exception))
        self.assertEqual(len(self.requests), 2)

    def test_fallback_timeout_propagates_without_another_attempt(self):
        self.use_responses(completion("invalid"), httpx.ReadTimeout("timeout"))
        with self.assertRaises(openai.APITimeoutError):
            OntologyGenerator().llm_client.chat_json(messages=[])
        self.assertEqual(len(self.requests), 2)

    def test_logs_sanitize_credentials_and_document_contents(self):
        self.use_responses(completion(json.dumps(ONTOLOGY)))
        llm = LLMClient(base_url="https://user:password@provider.test/custom?key=secret#fragment")
        with self.assertLogs("mirofish.llm_client", level=logging.INFO) as logs:
            llm.chat_json(messages=[{"role": "user", "content": "private document"}])
        output = '\n'.join(logs.output)
        self.assertIn("endpoint=https://provider.test/custom", output)
        self.assertIn("elapsed=", output)
        for secret in ("user", "password", "secret", "private document", "fragment"):
            self.assertNotIn(secret, output)


class OntologyEndpointTests(unittest.TestCase):
    setUp = OntologyRequestTests.setUp
    use_responses = OntologyRequestTests.use_responses

    @classmethod
    def setUpClass(cls):
        from app import create_app
        cls.app = create_app()

    def post(self, record_run):
        return self.app.test_client().post('/api/graph/ontology/generate', data={
            "files": (io.BytesIO(b"A school plans to open a library."), "seed.txt"),
            "simulation_requirement": "Predict reactions to the library.",
            "record_run": str(record_run).lower(),
        })

    def test_flask_success_errors_persistence_and_recording(self):
        cases = (
            (lambda: [completion(json.dumps(ONTOLOGY))], 200, 1),
            (lambda: [httpx.ReadTimeout("private timeout detail")], 504, 1),
            (lambda: [httpx.ConnectError("private connection detail")], 502, 1),
            (lambda: [httpx.Response(401, json={"error": {"message": "private auth detail"}})], 502, 1),
            (lambda: [httpx.Response(429, json={"error": {"message": "private quota detail"}})], 502, 1),
            (lambda: [httpx.Response(503, json={"error": {"message": "private provider detail"}})], 502, 1),
            (lambda: [completion("invalid"), completion("invalid")], 502, 2),
            (lambda: [completion('{"entity_types": [null]}')], 502, 1),
            (lambda: [completion('{}')], 502, 1),
        )
        for record_run in (False, True):
            for outcomes, status, calls in cases:
                with self.subTest(record_run=record_run, status=status):
                    self.requests.clear()
                    self.use_responses(*outcomes())
                    response = self.post(record_run)
                    self.assertEqual(response.status_code, status, response.json)
                    self.assertEqual(len(self.requests), calls)
                    data = response.json
                    project_id = data['data']['project_id']
                    project = ProjectManager.get_project(project_id)
                    self.assertEqual(project.status, ProjectStatus.ONTOLOGY_GENERATED if status == 200 else ProjectStatus.FAILED)
                    self.assertEqual(project.record_run, record_run)
                    self.assertTrue(list((self.root / 'projects' / project_id / 'files').iterdir()))
                    self.assertTrue((self.root / 'projects' / project_id / 'extracted_text.txt').exists())
                    draft = self.root / 'recordings' / 'drafts' / project_id
                    self.assertEqual(draft.exists(), record_run)
                    if status != 200:
                        self.assertFalse(data['success'])
                        self.assertEqual(data['error'], project.error)
                        self.assertNotIn('private', data['error'])
                        self.assertNotIn('traceback', data)
                    else:
                        self.assertTrue(data['success'])
                        self.assertEqual(set(data['data']), {'project_id', 'project_name', 'ontology', 'analysis_summary', 'files', 'total_text_length'})
                    if record_run:
                        saved = json.loads((draft / 'project.json').read_text())
                        self.assertEqual(saved['status'], project.status.value)
                        self.assertTrue((draft / 'events.jsonl').exists())
                        self.assertTrue(list((draft / 'project' / 'files').iterdir()))


if __name__ == '__main__':
    unittest.main()
