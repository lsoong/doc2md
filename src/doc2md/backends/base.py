"""변환 엔진 공통 인터페이스.

엔진 추가 방법:
  1. Backend 를 상속한 클래스를 이 패키지에 만든다.
  2. name / title / extensions / install_hint 를 채운다.
  3. is_available() 이 True 일 때만 convert() 가 불린다.
  4. backends/__init__.py 의 ENGINE_CLASSES 에 등록한다.
"""

from __future__ import annotations

import importlib.util
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config


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
    # LLM 후처리 여부
    llm_refined: bool = False


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
    # 원격 LLM 호출이 필요한 엔진인가
    needs_llm: bool = False

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

    def _result(self, markdown: str, path: Path, **kwargs: Any) -> ConversionResult:
        return ConversionResult(markdown=markdown, engine=self.name, source=path, **kwargs)
