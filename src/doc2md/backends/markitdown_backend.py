"""MarkItDown (Microsoft) 엔진.

가장 넓은 포맷을 커버하고 빠르다. Office 포맷(docx/pptx/xlsx)은 원본이 이미 구조를
갖고 있어 결과가 좋지만, PDF 는 텍스트만 뽑아내므로 표·다단 레이아웃은 약하다.
"""

from __future__ import annotations

import time
from pathlib import Path

from .base import Backend, BackendUnavailable, ConversionResult


class MarkItDownBackend(Backend):
    name = "markitdown"
    title = "MarkItDown (Microsoft)"
    extensions = (
        ".pdf",
        ".docx",
        ".doc",
        ".pptx",
        ".ppt",
        ".xlsx",
        ".xls",
        ".csv",
        ".html",
        ".htm",
        ".txt",
        ".json",
        ".xml",
        ".epub",
        ".zip",
    )
    install_hint = "pip install 'markitdown[all]'"
    priority = 20
    requires = ("markitdown",)

    def convert(self, path: Path) -> ConversionResult:
        try:
            from markitdown import MarkItDown
        except ImportError as exc:  # pragma: no cover - is_available 로 걸러짐
            raise BackendUnavailable(self.install_hint) from exc

        started = time.perf_counter()
        converter = MarkItDown(enable_plugins=bool(self.option("plugins", False)))
        result = converter.convert(str(path))
        # 버전에 따라 markdown / text_content 중 하나를 쓴다.
        markdown = getattr(result, "markdown", None) or getattr(result, "text_content", "") or ""
        warnings: list[str] = []
        if path.suffix.lower() == ".pdf":
            warnings.append(
                "MarkItDown 의 PDF 변환은 표·다단 레이아웃 보존이 약합니다. "
                "정확도가 중요하면 docling/marker/mineru 엔진을 쓰세요."
            )
        return self._result(
            markdown.strip(),
            path,
            elapsed=time.perf_counter() - started,
            warnings=warnings,
        )
