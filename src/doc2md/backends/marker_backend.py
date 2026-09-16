"""Marker (datalab) 엔진.

배치 처리량이 가장 좋고 수식·코드 블록 복원이 강하다. GPU 가 있으면 페이지당 처리
속도가 크게 벌어진다. 모델은 첫 실행 때 내려받는다.
"""

from __future__ import annotations

import time
from pathlib import Path

from .base import Backend, BackendUnavailable, ConversionResult

# 모델 로딩이 수 초 걸리므로 프로세스 안에서 한 번만 만든다.
_MODEL_CACHE: dict | None = None


class MarkerBackend(Backend):
    name = "marker"
    title = "Marker (datalab)"
    extensions = (".pdf", ".docx", ".pptx", ".xlsx", ".epub", ".html")
    install_hint = "pip install marker-pdf"
    priority = 70
    requires = ("marker",)

    def convert(self, path: Path) -> ConversionResult:
        global _MODEL_CACHE
        try:
            from marker.converters.pdf import PdfConverter
            from marker.models import create_model_dict
            from marker.output import text_from_rendered
        except ImportError as exc:  # pragma: no cover
            raise BackendUnavailable(self.install_hint) from exc

        started = time.perf_counter()
        if _MODEL_CACHE is None:
            _MODEL_CACHE = create_model_dict()

        config = {"output_format": "markdown"}
        if self.option("force_ocr", False):
            config["force_ocr"] = True
        if self.option("page_range"):
            config["page_range"] = self.option("page_range")

        converter = PdfConverter(artifact_dict=_MODEL_CACHE, config=config)
        rendered = converter(str(path))
        markdown, _, images = text_from_rendered(rendered)

        image_bytes: dict[str, bytes] = {}
        for name, image in (images or {}).items():
            buf = _pil_to_png(image)
            if buf is not None:
                image_bytes[name] = buf

        return self._result(
            markdown.strip(),
            path,
            elapsed=time.perf_counter() - started,
            images=image_bytes,
        )


def _pil_to_png(image: object) -> bytes | None:
    """marker 가 돌려주는 PIL 이미지를 PNG 바이트로."""
    save = getattr(image, "save", None)
    if save is None:
        return None
    import io

    buf = io.BytesIO()
    save(buf, format="PNG")
    return buf.getvalue()
