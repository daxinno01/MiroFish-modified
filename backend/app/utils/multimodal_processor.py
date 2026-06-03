import os
import base64
from typing import Optional
from app.utils import LLMClient
from ..config import Config
from ..utils.file_parser import FileParser

class MultimodalProcessor:
    """Handles image analysis and description persistence."""

    MAP_CONSTRUCTION_DIR = os.path.join(Config.PROMPTS_FOLDER, '1 - graph_construction')

    def __init__(self, simulation_id: Optional[str] = None, project_id: Optional[str] = None):
        self.llm = LLMClient(simulation_id=simulation_id, project_id=project_id)

    def describe_image(self, base64_img, short_tag: str) -> str:
        system_prompt = FileParser._extract_from_txt(
            os.path.join(self.MAP_CONSTRUCTION_DIR, "image_to_text_system_prompt.txt")
        ).format(
            short_tag=short_tag
        )

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": system_prompt
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

