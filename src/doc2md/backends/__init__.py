"""엔진 레지스트리."""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from .base import Backend, BackendUnavailable, ConversionResult
from .docling_backend import DoclingBackend
from .marker_backend import MarkerBackend
from .markitdown_backend import MarkItDownBackend
from .mineru_backend import MinerUBackend
from .pymupdf_backend import PyMuPDFBackend
from .vlm_backend import VLMBackend

ENGINE_CLASSES: tuple[type[Backend], ...] = (
    MinerUBackend,
    DoclingBackend,
    MarkerBackend,
    VLMBackend,
    PyMuPDFBackend,
    MarkItDownBackend,
)

ENGINES: dict[str, type[Backend]] = {cls.name: cls for cls in ENGINE_CLASSES}


class EngineNotFound(RuntimeError):
    pass


def get_engine(name: str, cfg: Config | None = None) -> Backend:
    try:
        cls = ENGINES[name]
    except KeyError:
        raise EngineNotFound(
            f"알 수 없는 엔진: {name}\n  사용 가능: {', '.join(ENGINES)}"
        ) from None
    if not cls.is_available():
        raise BackendUnavailable(f"{cls.title} 이(가) 설치되지 않았습니다. 설치: {cls.install_hint}")
    return cls(cfg)


def engines_for(path: Path, *, only_available: bool = True) -> list[type[Backend]]:
    """해당 파일 포맷을 지원하는 엔진을 우선순위 높은 순으로."""
    found = [cls for cls in ENGINE_CLASSES if cls.supports(path)]
    if only_available:
        found = [cls for cls in found if cls.is_available()]
    return sorted(found, key=lambda c: c.priority, reverse=True)


def pick_auto(path: Path, cfg: Config | None = None) -> Backend:
    """포맷에 맞는 가장 정확한 엔진을 자동 선택한다.

    LLM 호출이 필요한 엔진(vlm)은 비용이 들어 자동 선택 대상에서 제외한다 —
    쓰려면 --engine vlm 으로 명시한다.
    """
    candidates = [cls for cls in engines_for(path) if not cls.needs_llm]
    if not candidates:
        supported = engines_for(path, only_available=False)
        if supported:
            hints = "\n".join(f"  - {c.title}: {c.install_hint}" for c in supported)
            raise BackendUnavailable(
                f"{path.suffix} 를 지원하는 엔진이 설치돼 있지 않습니다. 설치 후보:\n{hints}"
            )
        raise BackendUnavailable(f"지원하지 않는 포맷입니다: {path.suffix}")
    return candidates[0](cfg)


__all__ = [
    "Backend",
    "BackendUnavailable",
    "ConversionResult",
    "ENGINES",
    "ENGINE_CLASSES",
    "EngineNotFound",
    "engines_for",
    "get_engine",
    "pick_auto",
]
