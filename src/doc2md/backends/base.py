"""변환 엔진 공통 인터페이스.

엔진 추가 방법:
  1. Backend 를 상속한 클래스를 이 패키지에 만든다.
  2. name / title / extensions / install_hint 를 채운다.
  3. is_available() 이 True 일 때만 convert() 가 불린다.
  4. 엔진 자체에 LLM 연결 기능이 있으면 llm_hooks 를 채운다.
  5. backends/__init__.py 의 ENGINE_CLASSES 에 등록한다.
"""

from __future__ import annotations

import importlib.util
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..config import Config, ConfigError, LLMConfig, ensure_self_hosted


@dataclass(frozen=True)
class LLMHook:
    """엔진이 자체적으로 제공하는 LLM 연결 방식 하나."""

    # --llm-mode 로 고르는 이름
    mode: str
    # 사람이 읽을 설명 (doc2md engines --llm 에 나온다)
    summary: str
    # "vision" 이면 기본 모델로 vision_model 을, "text" 면 model 을 쓴다
    role: str = "vision"
    # --llm-mode 를 생략했을 때 쓸 훅
    default: bool = False
    # 이 훅이 사내 게이트웨이(OpenAI 호환)를 직접 호출하는가.
    # False 면 엔진이 별도 추론 서버를 가리킨다(MinerU VLM 서버 등).
    uses_gateway: bool = True


@dataclass
class ConversionResult:
    """엔진 한 번의 변환 결과."""

    markdown: str
    engine: str
    source: Path
    elapsed: float = 0.0
    pages: int | None = None
    warnings: list[str] = field(default_factory=list)
    # 추출한 이미지: {파일명: 바이트}
    images: dict[str, bytes] = field(default_factory=dict)
    # LLM 후처리(--refine) 여부
    llm_refined: bool = False
    # 엔진 내장 LLM 연결을 썼다면 그 모드 이름 ("caption", "vlm" 등)
    engine_llm: str = ""


class BackendUnavailable(RuntimeError):
    """엔진이 설치되지 않았거나 이 포맷을 지원하지 않을 때."""


class Backend(ABC):
    name: str = ""
    title: str = ""
    # 지원 확장자 (소문자, 점 포함)
    extensions: tuple[str, ...] = ()
    install_hint: str = ""
    # 같은 포맷을 여러 엔진이 지원할 때 auto 선택 우선순위 (높을수록 먼저)
    priority: int = 0
    # 이 엔진이 필요로 하는 파이썬 모듈 (설치 여부 판정용)
    requires: tuple[str, ...] = ()
    # 원격 LLM 호출이 필요한 엔진인가 (켜 두면 auto 선택에서 빠진다)
    needs_llm: bool = False
    # 엔진에 내장된 LLM 연결 방식들. 비어 있으면 --engine-llm 을 못 쓴다.
    llm_hooks: tuple[LLMHook, ...] = ()

    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg or Config()
        self.options: dict[str, Any] = dict(self.cfg.engine_options.get(self.name, {}))

    @classmethod
    def is_available(cls) -> bool:
        return all(importlib.util.find_spec(mod) is not None for mod in cls.requires)

    @classmethod
    def supports(cls, path: Path) -> bool:
        return path.suffix.lower() in cls.extensions

    @abstractmethod
    def convert(self, path: Path) -> ConversionResult:
        """문서 하나를 Markdown 으로 변환한다."""

    # 편의 함수 ---------------------------------------------------------
    def option(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)

    # LLM 연결 ----------------------------------------------------------
    @classmethod
    def default_hook(cls) -> LLMHook | None:
        for hook in cls.llm_hooks:
            if hook.default:
                return hook
        return cls.llm_hooks[0] if cls.llm_hooks else None

    @property
    def llm_enabled(self) -> bool:
        """이 엔진의 내장 LLM 연결을 켜야 하는가."""
        return bool(self.llm_hooks) and bool(self.option("use_llm", False))

    def resolve_hook(self) -> LLMHook:
        """--llm-mode / engines.<이름>.llm_mode 로 고른 훅을 돌려준다."""
        mode = str(self.option("llm_mode", "") or "")
        if not mode:
            hook = self.default_hook()
            if hook is None:
                raise ConfigError(f"{self.title} 에는 내장 LLM 연결이 없습니다.")
            return hook
        for hook in self.llm_hooks:
            if hook.mode == mode:
                return hook
        available = ", ".join(h.mode for h in self.llm_hooks) or "(없음)"
        raise ConfigError(
            f"{self.title} 이(가) 모르는 llm_mode 입니다: {mode}\n  사용 가능: {available}"
        )

    def llm_model_for(self, hook: LLMHook) -> str:
        """훅이 쓸 모델 ID. 엔진별 지정 > 역할별 기본값."""
        explicit = str(self.option("llm_model", "") or "")
        if explicit:
            return explicit
        if hook.role == "vision":
            return self.cfg.llm.effective_vision_model
        return self.cfg.llm.model

    def gateway_for(self, hook: LLMHook) -> tuple[str, str]:
        """게이트웨이를 직접 부르는 훅에 필요한 (사내 OpenAI 호환 주소, 모델 ID)."""
        url = self.cfg.llm.base_url.rstrip("/")
        if not url:
            raise ConfigError(
                f"{self.title} 의 내장 LLM 연결에 쓸 사내 게이트웨이 주소가 없습니다.\n"
                "  doc2md config init 으로 설정파일을 만들거나 --base-url 을 지정하세요."
            )
        # 엔진이 직접 HTTP 를 치는 경로라 여기서 한 번 더 막는다.
        ensure_self_hosted(url, f"{self.title} 의 내장 LLM 주소")
        model = self.llm_model_for(hook)
        if not model:
            raise ConfigError(
                f"{self.title} 의 내장 LLM 연결에 쓸 모델이 없습니다.\n"
                "  --llm-model 로 지정하거나 설정파일 [llm] 의 model / vision_model 을 채우세요."
            )
        return url, model

    def gateway_llm_config(self, hook: LLMHook) -> LLMConfig:
        """엔진 훅이 쓸 LLM 설정. 사내 게이트웨이 주소·모델로 맞춰 준다."""
        url, model = self.gateway_for(hook)
        return replace(self.cfg.llm, base_url=url, model=model)

    def _result(self, markdown: str, path: Path, **kwargs: Any) -> ConversionResult:
        return ConversionResult(markdown=markdown, engine=self.name, source=path, **kwargs)


_FENCE_RE = re.compile(r"^```(?:markdown|md)?[ \t]*\n(.*)\n```$", flags=re.DOTALL)


def strip_fence(text: str) -> str:
    """모델이 결과 전체를 ```markdown 펜스로 감싸는 경우를 벗겨낸다."""
    match = _FENCE_RE.match(text.strip())
    return match.group(1) if match else text
