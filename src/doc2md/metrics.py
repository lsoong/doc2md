"""변환 결과 비교용 지표.

정답지가 없는 상태에서 엔진을 고르기 위한 것이다. 점수가 높다고 무조건 정확한 건
아니고 "무엇이 얼마나 살아남았는지"를 보여 준다. 최종 판단은 사람이 눈으로 하거나
`compare --judge` 로 사내 모델에게 시킨다.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

HEADING_RE = re.compile(r"^(#{1,6})\s+\S", re.MULTILINE)
LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+\S", re.MULTILINE)
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$", re.MULTILINE)
IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
CODE_FENCE_RE = re.compile(r"^```", re.MULTILINE)
MATH_RE = re.compile(r"\$\$?[^$\n]+\$\$?")
HTML_TABLE_RE = re.compile(r"<table[\s>]", re.IGNORECASE)
# 잡음 후보: 단독 숫자 줄(쪽번호), "- 3 -" 같은 줄
NOISE_LINE_RE = re.compile(r"^\s*[-–—]?\s*\d{1,4}\s*[-–—]?\s*$", re.MULTILINE)


@dataclass
class Metrics:
    chars: int
    words: int
    lines: int
    headings: int
    list_items: int
    table_blocks: int
    table_rows: int
    html_tables: int
    images: int
    code_blocks: int
    math: int
    noise_lines: int
    elapsed: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


def measure(markdown: str, elapsed: float = 0.0) -> Metrics:
    return Metrics(
        chars=len(markdown),
        words=len(markdown.split()),
        lines=len(markdown.splitlines()),
        headings=len(HEADING_RE.findall(markdown)),
        list_items=len(LIST_RE.findall(markdown)),
        table_blocks=_table_blocks(markdown),
        table_rows=len(TABLE_ROW_RE.findall(markdown)),
        html_tables=len(HTML_TABLE_RE.findall(markdown)),
        images=len(IMAGE_RE.findall(markdown)),
        code_blocks=len(CODE_FENCE_RE.findall(markdown)) // 2,
        math=len(MATH_RE.findall(markdown)),
        noise_lines=len(NOISE_LINE_RE.findall(markdown)),
        elapsed=round(elapsed, 2),
    )


def _table_blocks(markdown: str) -> int:
    """헤더 구분선(|---|)을 가진 제대로 된 표의 개수."""
    return len(TABLE_SEP_RE.findall(markdown)) + len(HTML_TABLE_RE.findall(markdown))


def table_health(markdown: str) -> float:
    """표 행 중 열 개수가 일정한 비율 (0~1). 표가 없으면 1.0."""
    rows = [r for r in TABLE_ROW_RE.findall(markdown)]
    if not rows:
        return 1.0
    counts: dict[int, int] = {}
    for row in rows:
        n = row.strip().strip("|").count("|") + 1
        counts[n] = counts.get(n, 0) + 1
    return max(counts.values()) / len(rows)


def summarize(results: dict[str, str], elapsed: dict[str, float] | None = None) -> dict[str, dict]:
    """{엔진명: markdown} → {엔진명: 지표 dict}"""
    elapsed = elapsed or {}
    out: dict[str, dict] = {}
    for engine, markdown in results.items():
        metrics = measure(markdown, elapsed.get(engine, 0.0))
        data = metrics.as_dict()
        data["table_health"] = round(table_health(markdown), 3)
        out[engine] = data
    return out


def to_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
