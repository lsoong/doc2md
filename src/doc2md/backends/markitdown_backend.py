"""MarkItDown (Microsoft) 엔진.

가장 넓은 포맷을 커버하고 빠르다. Office 포맷(docx/pptx/xlsx)은 원본이 이미 구조를
갖고 있어 결과가 좋지만, PDF 는 텍스트만 뽑아내므로 표·다단 레이아웃은 약하다.

내장 LLM 연결: MarkItDown 은 `llm_client` / `llm_model` / `llm_prompt` 를 받아 그림에
설명을 붙인다(이미지 파일 자체, PPT 슬라이드 안의 그림). OpenAI 클라이언트 객체를
기대하지만 실제로 쓰는 건 `client.chat.completions.create` 하나뿐이라, 사내 게이트웨이를
감싼 OpenAICompatClient 를 그대로 끼워 넣는다 — openai 패키지가 없어도 된다.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..llm import CAPTION_PROMPT, LLMClient, OpenAICompatClient
from .base import Backend, BackendUnavailable, ConversionResult, LLMHook


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
        ".png",
        ".jpg",
        ".jpeg",
    )
    install_hint = "pip install 'markitdown[all]'"
    priority = 20
    requires = ("markitdown",)
    license = "MIT"
    license_note = (
        "제약 없음. 다만 [all] 로 딸려오는 선택 의존성은 별도 확인이 필요하다"
        "(현재 설치본 기준 모두 MIT·BSD·Apache 계열)."
    )
    license_url = "https://github.com/microsoft/markitdown/blob/main/LICENSE"
    llm_hooks = (
        LLMHook(
            "caption",
            "이미지 파일과 PPT 슬라이드 그림에 사내 비전 모델이 설명을 붙인다",
            role="vision",
            default=True,
        ),
    )

    def convert(self, path: Path) -> ConversionResult:
        try:
            from markitdown import MarkItDown
        except ImportError as exc:  # pragma: no cover - is_available 로 걸러짐
            raise BackendUnavailable(self.install_hint) from exc

        started = time.perf_counter()
        warnings: list[str] = []
        shim: OpenAICompatClient | None = None
        used_mode = ""
        kwargs: dict = {"enable_plugins": bool(self.option("plugins", False))}

        if self.llm_enabled:
            hook = self.resolve_hook()
            llm_cfg = self.gateway_llm_config(hook)
            model = llm_cfg.model
            used_mode = hook.mode
            shim = OpenAICompatClient(LLMClient(llm_cfg))
            kwargs.update(
                llm_client=shim,
                llm_model=model,
                llm_prompt=str(self.option("llm_prompt", "") or CAPTION_PROMPT),
            )
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".pptx", ".ppt"}:
                warnings.append(
                    "MarkItDown 의 LLM 연결은 이미지 파일과 PPT 그림에만 붙습니다. "
                    f"{path.suffix} 에는 효과가 없으니 --refine 이나 docling --engine-llm 을 쓰세요."
                )

        try:
            converter = MarkItDown(**kwargs)
            result = converter.convert(str(path))
        finally:
            if shim is not None:
                shim.close()
        # 버전에 따라 markdown / text_content 중 하나를 쓴다.
        markdown = getattr(result, "markdown", None) or getattr(result, "text_content", "") or ""
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
            engine_llm=used_mode,
        )
