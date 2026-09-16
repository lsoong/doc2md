"""변환 파이프라인: 엔진 실행 → (선택) 사내 모델 정제 → 파일 저장."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .backends import Backend, ConversionResult, get_engine, pick_auto
from .config import Config
from .llm import REFINE_PROMPT, REFINE_SYSTEM, LLMClient

# 정제 요청 한 번에 보낼 최대 글자 수. 게이트웨이 컨텍스트에 맞춰 조절한다.
DEFAULT_CHUNK_CHARS = 6000

# 진행 상황을 한 줄씩 알리는 콜백
ProgressFn = Callable[[str], None]


def resolve_engine(name: str, path: Path, cfg: Config) -> Backend:
    if name in ("", "auto"):
        if cfg.default_engine not in ("", "auto"):
            return get_engine(cfg.default_engine, cfg)
        return pick_auto(path, cfg)
    return get_engine(name, cfg)


def convert_file(
    path: Path,
    *,
    engine: str = "auto",
    cfg: Config | None = None,
    refine: bool = False,
    refine_model: str | None = None,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    progress: ProgressFn | None = None,
) -> ConversionResult:
    """문서 하나를 Markdown 으로 변환한다."""
    cfg = cfg or Config()
    if not path.is_file():
        raise FileNotFoundError(f"파일이 없습니다: {path}")
    backend = resolve_engine(engine, path, cfg)
    if progress:
        progress(f"{path.name}: {backend.title} 엔진으로 변환 중")
    result = backend.convert(path)

    if refine and result.markdown.strip():
        if progress:
            progress(f"{path.name}: 사내 모델로 정제 중")
        result.markdown = refine_markdown(
            result.markdown,
            cfg=cfg,
            model=refine_model,
            chunk_chars=chunk_chars,
            progress=progress,
        )
        result.llm_refined = True
    return result


def refine_markdown(
    markdown: str,
    *,
    cfg: Config,
    model: str | None = None,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    progress: ProgressFn | None = None,
) -> str:
    """사내 모델로 Markdown 을 조각 단위로 정제한다."""
    chunks = split_markdown(markdown, chunk_chars)
    refined: list[str] = []
    with LLMClient(cfg.llm) as client:
        for index, chunk in enumerate(chunks, start=1):
            if progress and len(chunks) > 1:
                progress(f"  정제 {index}/{len(chunks)}")
            text = client.complete(
                REFINE_PROMPT.format(chunk=chunk),
                system=REFINE_SYSTEM,
                model=model or cfg.llm.model,
            )
            refined.append(_strip_fence(text).strip())
    return "\n\n".join(part for part in refined if part)


def split_markdown(markdown: str, chunk_chars: int) -> list[str]:
    """표·코드블록을 쪼개지 않으면서 대략 chunk_chars 크기로 나눈다."""
    blocks = _blocks(markdown)
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for block in blocks:
        if current and size + len(block) > chunk_chars:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        current.append(block)
        size += len(block) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks or [markdown]


def _blocks(markdown: str) -> list[str]:
    """빈 줄 기준으로 나누되 ``` 코드펜스 안은 유지한다."""
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if not in_fence and not line.strip():
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _strip_fence(text: str) -> str:
    stripped = text.strip()
    match = re.match(r"^```(?:markdown|md)?\s*\n(.*)\n```$", stripped, flags=re.DOTALL)
    return match.group(1) if match else text


@dataclass
class WriteResult:
    markdown_path: Path
    image_dir: Path | None
    image_count: int


def write_result(
    result: ConversionResult,
    out_dir: Path,
    *,
    stem: str | None = None,
    save_images: bool = True,
) -> WriteResult:
    """변환 결과를 out_dir 에 저장한다. 이미지가 있으면 <stem>_images/ 에 함께."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or result.source.stem
    md_path = out_dir / f"{stem}.md"
    md_path.write_text(result.markdown + "\n", encoding="utf-8")

    image_dir: Path | None = None
    count = 0
    if save_images and result.images:
        image_dir = out_dir / f"{stem}_images"
        image_dir.mkdir(parents=True, exist_ok=True)
        for name, data in result.images.items():
            (image_dir / Path(name).name).write_bytes(data)
            count += 1
    return WriteResult(markdown_path=md_path, image_dir=image_dir, image_count=count)


def output_stems(files: list[Path]) -> dict[Path, str]:
    """입력 파일마다 겹치지 않는 출력 파일명(확장자 제외)을 정한다.

    같은 이름의 다른 포맷(보고서.pdf, 보고서.docx)을 한 번에 변환하면 결과가
    서로를 덮어쓰므로, 이름이 겹칠 때만 확장자를 덧붙인다.
    """
    counts: dict[str, int] = {}
    for path in files:
        counts[path.stem] = counts.get(path.stem, 0) + 1
    stems: dict[Path, str] = {}
    used: set[str] = set()
    for path in files:
        stem = path.stem if counts[path.stem] == 1 else f"{path.stem}.{path.suffix.lstrip('.')}"
        # 디렉터리가 달라 여전히 겹치면 번호를 붙인다
        candidate, index = stem, 2
        while candidate in used:
            candidate = f"{stem}-{index}"
            index += 1
        used.add(candidate)
        stems[path] = candidate
    return stems


def collect_inputs(paths: list[Path], *, recursive: bool = False) -> list[Path]:
    """파일·디렉터리 인자를 실제 변환 대상 파일 목록으로 펼친다."""
    from .backends import ENGINE_CLASSES

    known = {ext for cls in ENGINE_CLASSES for ext in cls.extensions}
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            globber = path.rglob("*") if recursive else path.glob("*")
            files += sorted(
                p for p in globber if p.is_file() and p.suffix.lower() in known
            )
        else:
            files.append(path)
    return files
