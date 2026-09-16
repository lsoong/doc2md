"""Marker (datalab) 엔진.

배치 처리량이 가장 좋고 수식·코드 블록 복원이 강하다. GPU 가 있으면 페이지당 처리
속도가 크게 벌어진다. 모델은 첫 실행 때 내려받는다.

내장 LLM 연결: marker 의 `--use_llm`. 레이아웃 모델이 1차로 읽은 뒤, 애매한 표(쪽을
넘어가는 표·병합 셀)·수식·서식을 LLM 이 고쳐 준다. 페이지 전체를 LLM 에 맡기지 않아
비용 대비 정확도 개선이 크다.

marker 는 LLMService 구현으로 붙을 모델을 고르는데, 기본 제공 구현에는 Gemini·Claude·
Vertex 처럼 과금되는 외부 API 로 나가는 것들이 섞여 있다. doc2md 는 사내 자체 서빙
모델만 쓰므로 OpenAI 호환 서비스(= 사내 게이트웨이를 가리키는 OpenAIService)만
허용한다. marker 쪽이 openai 패키지를 쓰므로 `pip install openai` 가 함께 필요하다.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..config import ConfigError
from .base import Backend, BackendUnavailable, ConversionResult, LLMHook

# 모델 로딩이 수 초 걸리므로 프로세스 안에서 한 번만 만든다.
_MODEL_CACHE: dict | None = None

DEFAULT_LLM_SERVICE = "marker.services.openai.OpenAIService"
# 사내 게이트웨이(OpenAI 호환)로만 나가는 서비스. 이 목록 밖은 거부한다.
ALLOWED_LLM_SERVICES = frozenset({DEFAULT_LLM_SERVICE})


def check_llm_service(service: str) -> str:
    """marker 의 LLMService 가 사내 게이트웨이용인지 확인한다."""
    if service in ALLOWED_LLM_SERVICES:
        return service
    raise ConfigError(
        f"marker 의 llm_service 는 사내 게이트웨이용만 허용합니다: {service}\n"
        "  doc2md 는 사내에서 자체 서빙하는 모델만 연결합니다 — marker 의 Gemini·Claude·"
        "Vertex·Azure 서비스는 과금되는 외부 API 라 막혀 있습니다.\n"
        f"  허용: {', '.join(sorted(ALLOWED_LLM_SERVICES))} (기본값이므로 llm_service 를 "
        "비워 두면 됩니다)"
    )


class MarkerBackend(Backend):
    name = "marker"
    title = "Marker (datalab)"
    extensions = (".pdf", ".docx", ".pptx", ".xlsx", ".epub", ".html")
    install_hint = "pip install marker-pdf"
    priority = 70
    requires = ("marker",)
    llm_hooks = (
        LLMHook(
            "refine",
            "표 병합·수식·서식이 애매한 블록만 사내 모델이 다시 읽는다 (marker --use_llm)",
            role="vision",
            default=True,
        ),
    )

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

        used_mode = ""
        if self.llm_enabled:
            hook = self.resolve_hook()
            url, model = self.gateway_for(hook)
            config.update(
                marker_llm_config(
                    base_url=url,
                    model=model,
                    api_key=self.cfg.llm.api_key,
                    service=check_llm_service(
                        str(self.option("llm_service", "") or DEFAULT_LLM_SERVICE)
                    ),
                )
            )
            used_mode = hook.mode

        converter = PdfConverter(artifact_dict=_MODEL_CACHE, **self._converter_kwargs(config))
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
            engine_llm=used_mode,
        )

    def _converter_kwargs(self, config: dict) -> dict:
        """LLM 을 쓸 때는 marker 의 ConfigParser 로 서비스 객체까지 만들어 넘긴다."""
        if not config.get("use_llm"):
            return {"config": config}
        try:
            from marker.config.parser import ConfigParser
        except ImportError as exc:  # pragma: no cover - 아주 예전 marker
            raise BackendUnavailable(
                "이 marker 버전은 LLM 연결(ConfigParser)을 지원하지 않습니다. "
                "pip install -U marker-pdf"
            ) from exc

        parser = ConfigParser(config)
        try:
            service = parser.get_llm_service()
        except ImportError as exc:  # openai 패키지 미설치 등
            raise BackendUnavailable(
                f"marker 의 LLM 서비스를 못 만들었습니다: {exc}\n"
                "  OpenAI 호환 서비스에는 `pip install openai` 가 필요합니다."
            ) from exc
        return {
            "config": parser.generate_config_dict(),
            "llm_service": service,
            "processor_list": parser.get_processors(),
            "renderer": parser.get_renderer(),
        }


def marker_llm_config(*, base_url: str, model: str, api_key: str, service: str) -> dict:
    """marker 가 사내 게이트웨이를 보게 하는 설정 조각.

    marker 의 OpenAIService 는 openai SDK 를 그대로 쓰므로 base_url 은 /v1 루트를,
    api_key 는 빈 문자열이 아닌 값을 요구한다(인증이 없는 사내 게이트웨이면 아무 값).
    """
    return {
        "use_llm": True,
        "llm_service": check_llm_service(service),
        "openai_base_url": base_url,
        "openai_model": model,
        "openai_api_key": api_key or "no-key",
    }


def _pil_to_png(image: object) -> bytes | None:
    """marker 가 돌려주는 PIL 이미지를 PNG 바이트로."""
    save = getattr(image, "save", None)
    if save is None:
        return None
    import io

    buf = io.BytesIO()
    save(buf, format="PNG")
    return buf.getvalue()
