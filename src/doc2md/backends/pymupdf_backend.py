"""PyMuPDF4LLM 엔진.

텍스트 레이어가 살아 있는 PDF 에 대해 가장 빠르다(페이지당 수십 ms). 제목 추정과
간단한 표 인식을 해 주지만, 스캔 PDF 나 복잡한 표에는 쓰지 않는 게 좋다.
"""

from __future__ import annotations

import time
from pathlib import Path

from .base import Backend, BackendUnavailable, ConversionResult


class PyMuPDFBackend(Backend):
    name = "pymupdf4llm"
    title = "PyMuPDF4LLM"
    extensions = (".pdf", ".xps", ".epub", ".mobi", ".fb2", ".cbz")
    install_hint = "pip install pymupdf4llm"
    priority = 30
    requires = ("pymupdf4llm",)

    def convert(self, path: Path) -> ConversionResult:
        try:
            import pymupdf
            import pymupdf4llm
        except ImportError as exc:  # pragma: no cover
            raise BackendUnavailable(self.install_hint) from exc

        started = time.perf_counter()
        markdown = pymupdf4llm.to_markdown(
            str(path),
            page_chunks=False,
            show_progress=False,
            table_strategy=self.option("table_strategy", "lines_strict"),
        )
        with pymupdf.open(str(path)) as doc:
            pages = doc.page_count
        warnings: list[str] = []
        if len(markdown.strip()) < 40 * max(pages, 1):
            warnings.append(
                "추출된 텍스트가 거의 없습니다. 스캔 이미지 PDF 로 보입니다 — "
                "docling(OCR) 이나 vlm 엔진을 쓰세요."
            )
        return self._result(
            markdown.strip(),
            path,
            elapsed=time.perf_counter() - started,
            pages=pages,
            warnings=warnings,
        )
