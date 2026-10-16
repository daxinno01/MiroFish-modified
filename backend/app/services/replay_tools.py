"""Report chat retrieval over saved graph and source text, without graph services."""
from dataclasses import dataclass
import json
import re

from .replay import ReplayView


@dataclass
class SavedSearchResult:
    data: dict

    def to_dict(self):
        return self.data

    def to_text(self):
        return json.dumps(self.data, ensure_ascii=False, indent=2)


class SavedToolsService:
    def __init__(self, session):
        self.view = ReplayView(session)

    def search_graph(self, graph_id, query, limit=10, **kwargs):
        graph = self.view.graph()
        words = re.findall(r"\w+", query.lower())

        def rank(item):
            text = json.dumps(item, ensure_ascii=False).lower()
            return sum(word in text for word in words)

        nodes = sorted(graph.get("nodes", []), key=rank, reverse=True)[:limit]
        edges = sorted(graph.get("edges", []), key=rank, reverse=True)[:limit]
        text = (self.view.folder / "project" / "extracted_text.txt").read_text(encoding="utf-8")
        chunks = [text[i:i + 1500] for i in range(0, len(text), 1500)]
        passages = sorted(chunks, key=rank, reverse=True)[:min(limit, 5)]
        return SavedSearchResult({"query": query, "nodes": nodes, "edges": edges,
                                  "facts": [e.get("fact", "") for e in edges],
                                  "source_passages": passages, "total_count": len(nodes) + len(edges) + len(passages)})

    def quick_search(self, graph_id, query, limit=10, **kwargs):
        return self.search_graph(graph_id, query, limit)

    def insight_forge(self, graph_id, query, **kwargs):
        return self.search_graph(graph_id, query, 20)

    def panorama_search(self, graph_id, query, include_expired=True, **kwargs):
        result = self.search_graph(graph_id, query, 100)
        if not include_expired:
            result.data["edges"] = [e for e in result.data["edges"] if not e.get("expired_at") and not e.get("invalid_at")]
            result.data["facts"] = [e.get("fact", "") for e in result.data["edges"]]
        return result

    def get_graph_statistics(self, graph_id):
        graph = self.view.graph()
        return {"node_count": len(graph.get("nodes", [])), "edge_count": len(graph.get("edges", []))}

    def get_entity_summary(self, graph_id, entity_name):
        return {"entities": [n for n in self.view.graph().get("nodes", []) if n.get("name", "").casefold() == entity_name.casefold()]}

    def get_entities_by_type(self, graph_id, entity_type):
        return [SavedSearchResult(n) for n in self.view.graph().get("nodes", []) if entity_type in n.get("labels", [])]

    def interview_agents(self, simulation_id, **kwargs):
        # Reuse the existing interview adapter without constructing its graph store.
        from .local_tools import LocalToolsService
        from .simulation_runner import SimulationRunner
        SimulationRunner.ensure_interaction_runner(simulation_id)
        adapter = LocalToolsService.__new__(LocalToolsService)
        return adapter.interview_agents(simulation_id, **kwargs)
