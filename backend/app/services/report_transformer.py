"""
Report Transformation Service
"""
import os
from markdown_it import MarkdownIt
from google.cloud import translate_v3
from weasyprint import HTML
import tempfile
import requests
import logging
from ..utils.html_to_pdf_template import HTML_TO_PDF_TEMPLATE

logger = logging.getLogger("mirofish.services.report_transformer")

class ReportTransformer:
    
    @classmethod
    def translate_and_export(cls, md_content: str):
        logger.info("Converting report to HTML...")
        html_content = cls._markdown_to_html(md_content)

        logger.info("Chunking HTML content...")
        html_chunks = cls._split_into_chunks(text=html_content)

        logger.info("Translating chunks into English...")
        translated_content = cls._translate(html_chunks)

        logger.info("Exporting to PDF...")
        # Basic CSS for a clean "Bank-style" report
        styled_html = HTML_TO_PDF_TEMPLATE.format(translated_content=translated_content)
        
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as output:
            HTML(string=styled_html).write_pdf(output.name)
            return output.name

    @classmethod
    def _translate(cls, html_chunks: list[str]) -> str:
        client = translate_v3.TranslationServiceClient()
        project_id = os.getenv("GCP_PROJECT_ID")
        
        if not project_id:
            logger.error("GCP_PROJECT_ID env var not set!")
            raise ValueError("GCP_PROJECT_ID env var not set!")

        # 修复了 f-string 中的引号冲突
        parent = f"projects/{project_id}/locations/global"
        translated_parts = []
        
        for chunk in html_chunks:
            if not chunk.strip():
                continue
                
            try:
                response = client.translate_text(
                    request={
                        "parent": parent,
                        "contents": [chunk],
                        "mime_type": "text/html",
                        "target_language_code": "en",
                    }
                )
                translated_parts.append(response.translations[0].translated_text)
            except Exception as e:
                logger.error(f"Chunk translation failed: {str(e)}")
                # 如果某一块翻译失败，为了保证文档完整性，可以选择保留原内容
                translated_parts.append(chunk)

        return "\n".join(translated_parts)
    
    @classmethod
    def _markdown_to_html(cls, md_content: str) -> str:
        md = MarkdownIt("gfm-like")

        return md.render(md_content)
    
    @classmethod
    def _split_into_chunks(cls, text: str, chunk_size = 25000) -> list:
        chunks = []
        current_chunk = []
        current_length = 0

        for line in text.split('\n'):
            line_length = len(line) + 1 # +1 是回车符

            if current_length + line_length > chunk_size and current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_length = line_length
            else:
                current_chunk.append(line)
                current_length += line_length

        if current_chunk:
            chunks.append("\n".join(current_chunk))

        return chunks
