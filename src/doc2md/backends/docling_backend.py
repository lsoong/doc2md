"""Docling (IBM Research) 엔진.

레이아웃 모델 + TableFormer 로 표 구조를 복원한다. PDF 뿐 아니라 docx/pptx/xlsx/html
까지 같은 파이프라인으로 처리해 출력 스타일이 일관되는 것이 큰 장점이다. 첫 실행 때
모델 가중치를 내려받으므로(수백 MB) 폐쇄망에서는 미리 캐시를 옮겨 둬야 한다.
"""

from __future__ import annotations

import time
from pathlib import Path

from .base import Backend, BackendUnavailable, ConversionResult


class DoclingBackend(Backend):
    name = "docling"
    title = "Docling (IBM)"
    extensions = (
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
        ".html",
        ".htm",
        ".md",
        ".csv",
        ".png",
        ".jpg",
        ".jpeg",
        ".tiff",
    )
    install_hint = "pip install docling"
    priority = 80
    requires = ("docling",)

    def convert(self, path: Path) -> ConversionResult:
        try:
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
        except ImportError as exc:  # pragma: no cover
            raise BackendUnavailable(self.install_hint) from exc

        started = time.perf_counter()
        format_options = None
        if path.suffix.lower() == ".pdf":
            pipeline = PdfPipelineOptions()
            pipeline.do_ocr = bool(self.option("ocr", False))
            pipeline.do_table_structure = bool(self.option("table_structure", True))
            if pipeline.do_table_structure:
                pipeline.table_structure_options.do_cell_matching = True
            format_options = {InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline)}

        converter = DocumentConverter(format_options=format_options)
        result = converter.convert(str(path))
        markdown = result.document.export_to_markdown()

        pages = None
        try:
            pages = len(result.document.pages) or None
        except (AttributeError, TypeError):  # 포맷에 따라 pages 가 없다
            pages = None

        warnings: list[str] = []
        if path.suffix.lower() == ".pdf" and not self.option("ocr", False):
            if len(markdown.strip()) < 40 * max(pages or 1, 1):
                warnings.append(
                    "텍스트가 거의 없습니다. 스캔 PDF 라면 engines.docling.ocr = true 로 켜세요."
                )
        return self._result(
            markdown.strip(),
            path,
            elapsed=time.perf_counter() - started,
            pages=pages,
            warnings=warnings,
        )
