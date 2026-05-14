import os
import base64
from typing import Optional
from app.utils import LLMClient

class MultimodalProcessor:
    """Handles image analysis and description persistence."""

    def __init__(self, simulation_id: Optional[str] = None, project_id: Optional[str] = None):
        self.llm = LLMClient(simulation_id=simulation_id, project_id=project_id)

    def describe_image(self, base64_img, short_tag: str) -> str:
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"""\
Context: You are a high-fidelity visual-to-text analyst for a financial intelligence platform. The user has provided an image labeled with the following semantic anchor: "{short_tag}".

Objective: Convert this image into an extremely granular, structured text inventory. This text will be used to build a Knowledge Graph, so precision and entity-relation clarity are paramount.

Instructions:

1.  Executive Summary: Provide a summary of what this image represents in the context of the provided tag.

2.  OCR / Text Extraction: Transcribe all visible text. Maintain the hierarchy (headings vs. body) and include numerical data, currency symbols (e.g., S$, SGD), and percentages exactly as they appear.

3.  Entity Identification: List all distinct entities found (Organizations, People, Products, Financial Metrics, Geographic Locations).

4.  Structural Relationships: Describe how entities relate spatially or logically.
    Example: "In the organogram, 'Team A' (Entity) reports directly to the 'CTO' (Entity)."

5.  Visual Data Translation: If the image is a chart or table, describe the trends, axes, and specific data points (e.g., "The bar chart shows Q1 2026 revenue at S$4.2B, a 10% increase YoY").

Constraint: Do not be conversational. Use clear, declarative statements. If an element is ambiguous, describe its visual properties rather than guessing.
"""
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_img}"
                        }
                    }
                ]
            }
        ]

        return self.llm.chat(messages=messages, caller_hint="describe image")

    def _encode_image(self, image_path: str) -> str:
        """Encode the image to base64 string."""
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

