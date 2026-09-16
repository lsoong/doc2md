"""Docling (IBM Research) 엔진.

레이아웃 모델 + TableFormer 로 표 구조를 복원한다. PDF 뿐 아니라 docx/pptx/xlsx/html
까지 같은 파이프라인으로 처리해 출력 스타일이 일관되는 것이 큰 장점이다. 첫 실행 때
모델 가중치를 내려받으므로(수백 MB) 폐쇄망에서는 미리 캐시를 옮겨 둬야 한다.

내장 LLM 연결 두 가지 (`--llm-mode`):
  vlm      docling 의 VLM 파이프라인. 페이지 이미지를 사내 비전 모델에 보내 Markdown 을
           통째로 받는다. 레이아웃 모델을 쓰지 않으므로 가중치 없이도 돌아간다.
  picture  본문·표는 docling 이 읽고, 그림에만 사내 모델의 설명을 붙인다. 표 정확도는
           docling 그대로 두면서 차트·도식 내용을 살리고 싶을 때 쓴다.

둘 다 docling 이 OpenAI 호환 /chat/completions 를 직접 호출한다. 사내 게이트웨이가
Anthropic 방언뿐이면 [llm] openai_base_url 을 따로 지정해야 한다.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..llm import CAPTION_PROMPT, ENGINE_VISION_PROMPT, auth_headers
from .base import Backend, BackendUnavailable, ConversionResult, LLMHook

# docling 이 그림 자리에 남기는 표시. 여기에 사내 모델의 설명을 끼워 넣는다.
IMAGE_PLACEHOLDER = "<!-- image -->"
# VLM 파이프라인이 직접 다루는 포맷 (나머지는 docling 기본 경로를 탄다)
VLM_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff"}


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
    llm_hooks = (
        LLMHook(
            "vlm",
            "페이지 이미지를 사내 비전 모델이 통째로 읽어 Markdown 을 만든다",
            role="vision",
            default=True,
        ),
        LLMHook(
            "picture",
            "본문·표는 docling 이 읽고 그림 설명만 사내 모델이 붙인다",
            role="vision",
        ),
    )

    def convert(self, path: Path) -> ConversionResult:
        try:
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
        except ImportError as exc:  # pragma: no cover
            raise BackendUnavailable(self.install_hint) from exc

        started = time.perf_counter()
        warnings: list[str] = []
        suffix = path.suffix.lower()
        hook = self.resolve_hook() if self.llm_enabled else None
        used_mode = ""

        format_options: dict | None = None
        if hook is not None and hook.mode == "vlm":
            if suffix in VLM_EXTS:
                format_options = self._vlm_format_options(hook)
                used_mode = hook.mode
            else:
                warnings.append(
                    f"docling 의 VLM 파이프라인은 PDF·이미지에만 붙습니다 — {suffix} 는 "
                    "일반 경로로 변환했습니다. Office 문서에 모델을 붙이려면 --refine 을 쓰세요."
                )
        elif hook is not None and hook.mode == "picture":
            if suffix in {".pdf", ".docx", ".pptx", ".html", ".htm"}:
                used_mode = hook.mode
            else:
                warnings.append(f"{suffix} 에는 그림 설명을 붙일 대상이 없습니다.")

        if format_options is None and suffix == ".pdf":
            pipeline = PdfPipelineOptions()
            pipeline.do_ocr = bool(self.option("ocr", False))
            pipeline.do_table_structure = bool(self.option("table_structure", True))
            if pipeline.do_table_structure:
                pipeline.table_structure_options.do_cell_matching = True
            if used_mode == "picture":
                self._enable_picture_description(pipeline, hook)
            format_options = {InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline)}

        converter = DocumentConverter(format_options=format_options)
        result = converter.convert(str(path))
        markdown = result.document.export_to_markdown()

        if used_mode == "picture":
            markdown, described = inject_picture_descriptions(markdown, result.document)
            if not described:
                warnings.append("사내 모델이 설명을 붙일 그림을 찾지 못했습니다.")

        pages = None
        try:
            pages = len(result.document.pages) or None
        except (AttributeError, TypeError):  # 포맷에 따라 pages 가 없다
            pages = None

        if suffix == ".pdf" and not self.option("ocr", False) and not used_mode:
            if len(markdown.strip()) < 40 * max(pages or 1, 1):
                warnings.append(
                    "텍스트가 거의 없습니다. 스캔 PDF 라면 engines.docling.ocr = true 로 켜거나 "
                    "--engine-llm --llm-mode vlm 로 사내 비전 모델을 붙이세요."
                )
        return self._result(
            markdown.strip(),
            path,
            elapsed=time.perf_counter() - started,
            pages=pages,
            warnings=warnings,
            engine_llm=used_mode,
        )

    # ------------------------------------------------------------ LLM 연결
    def _vlm_format_options(self, hook: LLMHook) -> dict:
        """페이지 전체를 사내 비전 모델에 넘기는 VLM 파이프라인 구성."""
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import VlmPipelineOptions
        from docling.document_converter import ImageFormatOption, PdfFormatOption
        from docling.pipeline.vlm_pipeline import VlmPipeline

        pipeline_options = VlmPipelineOptions(
            enable_remote_services=True,
            vlm_options=self._vlm_options(hook),
        )
        return {
            InputFormat.PDF: PdfFormatOption(
                pipeline_cls=VlmPipeline, pipeline_options=pipeline_options
            ),
            InputFormat.IMAGE: ImageFormatOption(
                pipeline_cls=VlmPipeline, pipeline_options=pipeline_options
            ),
        }

    def _vlm_options(self, hook: LLMHook) -> Any:
        """docling 버전에 맞는 원격 VLM 옵션 객체를 만든다.

        2.120 이후의 새 구조(VlmConvertOptions + ApiVlmEngineOptions)를 먼저 쓰고,
        없으면 예전 구조(ApiVlmOptions)로 떨어진다.
        """
        from docling.datamodel.pipeline_options_vlm_model import ResponseFormat

        url, model = self.gateway_for(hook)
        prompt = str(self.option("llm_prompt", "") or ENGINE_VISION_PROMPT)
        common = {
            "url": chat_endpoint(url),
            "headers": auth_headers(self.cfg.llm),
            "params": {"model": model, "max_tokens": self.cfg.llm.max_tokens},
            "timeout": self.cfg.llm.timeout,
            "concurrency": int(self.option("concurrency", 1)),
        }
        scale = float(self.option("scale", 2.0))

        try:
            from docling.datamodel.pipeline_options import VlmConvertOptions
            from docling.datamodel.stage_model_specs import VlmModelSpec
            from docling.datamodel.vlm_engine_options import ApiVlmEngineOptions
        except ImportError:  # 예전 docling
            from docling.datamodel.pipeline_options_vlm_model import ApiVlmOptions

            return ApiVlmOptions(
                prompt=prompt,
                scale=scale,
                temperature=self.cfg.llm.temperature,
                response_format=ResponseFormat.MARKDOWN,
                **common,
            )

        return VlmConvertOptions(
            engine_options=ApiVlmEngineOptions(**common),
            model_spec=VlmModelSpec(
                name=model,
                # 원격 API 라 가중치를 내려받지 않는다. 표시용 이름만 채운다.
                default_repo_id=model,
                prompt=prompt,
                response_format=ResponseFormat.MARKDOWN,
                temperature=self.cfg.llm.temperature,
                max_new_tokens=self.cfg.llm.max_tokens,
            ),
            scale=scale,
        )

    def _enable_picture_description(self, pipeline: Any, hook: LLMHook) -> None:
        """본문은 docling 이 읽고 그림 설명만 사내 모델에 맡긴다."""
        from docling.datamodel.pipeline_options import PictureDescriptionApiOptions

        url, model = self.gateway_for(hook)
        pipeline.enable_remote_services = True
        pipeline.do_picture_description = True
        pipeline.generate_picture_images = True
        pipeline.images_scale = float(self.option("scale", 2.0))
        pipeline.picture_description_options = PictureDescriptionApiOptions(
            url=chat_endpoint(url),
            headers=auth_headers(self.cfg.llm),
            params={"model": model, "max_tokens": self.cfg.llm.max_tokens},
            prompt=str(self.option("llm_prompt", "") or CAPTION_PROMPT),
            timeout=self.cfg.llm.timeout,
            concurrency=int(self.option("concurrency", 1)),
            # 페이지 대비 이 비율보다 작은 그림은 건너뛴다. docling 기본값(0.05)은
            # 작은 차트·도식을 통째로 빠뜨리는 일이 있어 옵션으로 열어 둔다.
            picture_area_threshold=float(self.option("picture_min_area", 0.02)),
        )

def chat_endpoint(base_url: str) -> str:
    """게이트웨이 주소 → /chat/completions 엔드포인트."""
    base = base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def inject_picture_descriptions(markdown: str, document: Any) -> tuple[str, int]:
    """docling 이 남긴 그림 자리표시자에 사내 모델의 설명을 붙인다.

    설명을 본문에 이미 넣어 주는 docling(2.5x+)에서는 자리표시자만 alt 텍스트가
    있는 이미지 줄로 바꾸고 설명은 그대로 둔다 — 같은 문장을 두 번 쓰지 않는다.
    본문에 넣지 않는 버전에서는 설명 단락까지 여기서 만들어 붙인다.
    """
    texts = [_description_of(picture) for picture in getattr(document, "pictures", []) or []]
    if not any(texts):
        return markdown, 0

    # 자리표시자는 문서 순서대로 나오므로 pictures 순서와 1:1 로 맞춘다.
    parts = markdown.split(IMAGE_PLACEHOLDER)
    out = parts[0]
    described = 0
    for index, tail in enumerate(parts[1:]):
        text = (texts[index] if index < len(texts) else "").strip()
        if not text:
            out += IMAGE_PLACEHOLDER
        else:
            described += 1
            out += f"![{_one_line(text)}](image)"
            if text not in markdown:
                # 표·여러 줄 설명이 와도 깨지지 않게 인용 블록을 쓰지 않는다.
                out += f"\n\n**그림 설명(사내 모델):**\n\n{text}"
        out += tail
    return out, described


def _description_of(picture: Any) -> str:
    """docling 그림 항목에서 모델이 붙인 설명을 꺼낸다 (신·구 필드 모두)."""
    if hasattr(picture, "meta"):  # 2.5x 이후: meta.description
        description = getattr(picture.meta, "description", None)
        return str(getattr(description, "text", "") or "").strip()
    for annotation in getattr(picture, "annotations", []) or []:  # 예전 docling
        legacy = str(getattr(annotation, "text", "") or "").strip()
        if legacy:
            return legacy
    return ""


def _one_line(text: str, limit: int = 80) -> str:
    flat = " ".join(text.split())
    return flat[:limit] + ("…" if len(flat) > limit else "")
