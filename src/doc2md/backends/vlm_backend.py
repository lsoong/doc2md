"""사내 비전 모델(VLM) 엔진.

페이지를 이미지로 렌더링해 사내 게이트웨이의 비전 모델에 그대로 넘긴다. 스캔 PDF,
도장·수기 주석이 섞인 문서, 레이아웃이 기괴한 보고서처럼 규칙 기반 파서가 무너지는
문서에 쓴다. 문서가 사외로 나가지 않는다는 전제가 사내 게이트웨이라 가능한 방식이다.

주의: 페이지 수 × 이미지 토큰만큼 비용·시간이 든다. 기본으로 쓰지 말고 필요할 때만.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..llm import VISION_PROMPT, VISION_SYSTEM, ImagePart, LLMClient
from .base import Backend, BackendUnavailable, ConversionResult, LLMHook, strip_fence

# PDF 가 아닌 포맷은 PDF 로 바꾼 뒤 렌더링해야 한다.
OFFICE_EXTS = (".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls")


class VLMBackend(Backend):
    name = "vlm"
    title = "사내 비전 모델 (VLM)"
    extensions = (".pdf", ".png", ".jpg", ".jpeg", ".tiff")
    install_hint = "pip install pymupdf  (그리고 [llm] 설정에 vision_model 지정)"
    priority = 50
    requires = ("pymupdf",)
    needs_llm = True
    license = "doc2md 자체 코드(MIT) + PyMuPDF(AGPL-3.0)"
    license_note = (
        "페이지 렌더링에 PyMuPDF 를 쓰므로 pymupdf4llm 과 같은 AGPL 조건이 걸린다. "
        "변환 품질은 사내 비전 모델에 달려 있고, 그 모델의 라이선스는 별도로 확인해야 한다."
    )
    license_copyleft = True
    license_verdict = "사내 사용 가능 · 사외 배포/서비스면 AGPL 검토"
    license_url = "https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright"
    llm_hooks = (
        LLMHook(
            "page",
            "페이지를 이미지로 렌더링해 사내 비전 모델이 통째로 읽는다 (엔진 자체가 LLM)",
            role="vision",
            default=True,
        ),
    )

    def convert(self, path: Path) -> ConversionResult:
        try:
            import pymupdf
        except ImportError as exc:  # pragma: no cover
            raise BackendUnavailable(self.install_hint) from exc

        started = time.perf_counter()
        dpi = int(self.option("dpi", 200))
        max_pages = int(self.option("max_pages", 0))
        warnings: list[str] = []

        suffix = path.suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".tiff"}:
            page_images = [(1, path.read_bytes(), _media_type(suffix))]
        else:
            page_images = []
            with pymupdf.open(str(path)) as doc:
                total = doc.page_count
                limit = min(total, max_pages) if max_pages else total
                if limit < total:
                    warnings.append(f"max_pages={max_pages} 설정으로 {total}쪽 중 {limit}쪽만 변환했습니다.")
                for index in range(limit):
                    pix = doc.load_page(index).get_pixmap(dpi=dpi)
                    page_images.append((index + 1, pix.tobytes("png"), "image/png"))

        parts: list[str] = []
        with LLMClient(self.cfg.llm) as client:
            model = self.llm_model_for(self.resolve_hook())
            for page_no, data, media_type in page_images:
                text = client.complete(
                    VISION_PROMPT,
                    system=VISION_SYSTEM,
                    images=[ImagePart(data=data, media_type=media_type)],
                    model=model,
                )
                parts.append(strip_fence(text).strip())

        markdown = "\n\n".join(p for p in parts if p)
        return self._result(
            markdown,
            path,
            elapsed=time.perf_counter() - started,
            pages=len(page_images),
            warnings=warnings,
        )


def _media_type(suffix: str) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tiff": "image/tiff",
    }.get(suffix, "image/png")
