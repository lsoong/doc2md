"""doc2md 커맨드라인.

  doc2md engines                      설치된 엔진과 지원 포맷 확인
  doc2md models                       사내 게이트웨이의 모델 목록
  doc2md convert 보고서.pdf -e docling  변환
  doc2md compare 보고서.pdf            엔진별로 변환해 비교
  doc2md config init                  설정파일 생성
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import __version__
from .backends import (
    ENGINE_CLASSES,
    ENGINES,
    BackendUnavailable,
    EngineNotFound,
    engines_for,
)
from .config import SAMPLE_CONFIG, USER_CONFIG_PATH, ConfigError, load_config, override_llm
from .llm import JUDGE_PROMPT, JUDGE_SYSTEM, LLMClient, LLMError
from .metrics import summarize, to_json
from .pipeline import collect_inputs, convert_file, output_stems, write_result

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="PDF·Word·PPT·Excel 문서를 Markdown 으로 변환합니다 (멀티 엔진 + 사내 LLM 연동).",
)
console = Console()
err_console = Console(stderr=True)


def _fail(message: str) -> "typer.Exit":
    err_console.print(f"[bold red]오류[/] {escape(message)}")
    return typer.Exit(code=1)


def _load(config: str | None, api: str | None, base_url: str | None, model: str | None):
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        raise _fail(str(exc)) from exc
    return override_llm(cfg, api=api, base_url=base_url, model=model)


# --------------------------------------------------------------------- engines
@app.command()
def engines(
    fmt: str = typer.Option("", "--format", "-f", help="이 확장자를 지원하는 엔진만 (예: pdf)"),
) -> None:
    """설치된 변환 엔진과 지원 포맷을 보여 준다."""
    classes = list(ENGINE_CLASSES)
    if fmt:
        suffix = fmt if fmt.startswith(".") else f".{fmt}"
        classes = [c for c in classes if suffix.lower() in c.extensions]
        if not classes:
            raise _fail(f"{suffix} 를 지원하는 엔진이 없습니다.")

    table = Table(title="doc2md 변환 엔진 (우선순위 높은 순)")
    table.add_column("엔진", style="bold")
    table.add_column("이름")
    table.add_column("상태")
    table.add_column("우선순위", justify="right")
    table.add_column("지원 포맷")
    for cls in sorted(classes, key=lambda c: c.priority, reverse=True):
        available = cls.is_available()
        status = "[green]설치됨[/]" if available else f"[yellow]미설치[/] ({escape(cls.install_hint)})"
        if cls.needs_llm:
            status += " [cyan]+사내모델 필요[/]"
        table.add_row(
            cls.name,
            cls.title,
            status,
            str(cls.priority),
            " ".join(e.lstrip(".") for e in cls.extensions),
        )
    console.print(table)
    console.print("auto 선택 시 위 순서대로 설치된 첫 엔진을 씁니다 (vlm 은 명시할 때만).")


# ---------------------------------------------------------------------- models
@app.command()
def models(
    config: str = typer.Option(None, "--config", "-c", help="설정파일 경로"),
    api: str = typer.Option(None, "--api", help="openai | anthropic"),
    base_url: str = typer.Option(None, "--base-url", help="사내 게이트웨이 주소"),
) -> None:
    """사내 게이트웨이가 제공하는 모델 목록을 조회한다."""
    cfg = _load(config, api, base_url, None)
    if not cfg.llm.base_url:
        raise _fail("base_url 이 비어 있습니다. doc2md config init 으로 설정하세요.")
    try:
        with LLMClient(cfg.llm) as client:
            found = client.list_models()
    except (LLMError, ConfigError) as exc:
        raise _fail(str(exc)) from exc
    if not found:
        console.print("[yellow]게이트웨이가 모델 목록을 돌려주지 않았습니다.[/] --model 로 직접 지정하세요.")
        return
    table = Table(title=f"사내 모델 ({cfg.llm.base_url})")
    table.add_column("#", justify="right")
    table.add_column("모델 ID")
    table.add_column("현재 설정")
    for index, name in enumerate(found, start=1):
        marks = []
        if name == cfg.llm.model:
            marks.append("기본")
        if name == cfg.llm.effective_vision_model:
            marks.append("비전")
        table.add_row(str(index), name, ", ".join(marks))
    console.print(table)


def _pick_model(cfg) -> str:
    """대화식으로 모델을 고른다."""
    with LLMClient(cfg.llm) as client:
        found = client.list_models()
    if not found:
        raise _fail("고를 수 있는 모델이 없습니다. --model 로 직접 지정하세요.")
    for index, name in enumerate(found, start=1):
        console.print(f"  [bold]{index:2}[/] {name}")
    choice = typer.prompt("사용할 모델 번호", default="1")
    try:
        return found[int(choice) - 1]
    except (ValueError, IndexError):
        raise _fail(f"잘못된 선택: {choice}") from None


# --------------------------------------------------------------------- convert
@app.command()
def convert(
    paths: list[Path] = typer.Argument(..., help="변환할 파일 또는 디렉터리"),
    engine: str = typer.Option("auto", "--engine", "-e", help=f"{'|'.join(ENGINES)}|auto"),
    out: Path = typer.Option(Path("."), "--out", "-o", help="출력 디렉터리"),
    stdout: bool = typer.Option(False, "--stdout", help="파일 대신 표준출력으로"),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="디렉터리를 재귀 탐색"),
    refine: bool = typer.Option(False, "--refine", help="사내 모델로 결과를 다듬는다"),
    pick_model: bool = typer.Option(False, "--pick-model", help="모델을 대화식으로 고른다"),
    model: str = typer.Option(None, "--model", "-m", help="사내 모델 ID"),
    api: str = typer.Option(None, "--api", help="openai | anthropic"),
    base_url: str = typer.Option(None, "--base-url", help="사내 게이트웨이 주소"),
    config: str = typer.Option(None, "--config", "-c", help="설정파일 경로"),
    no_images: bool = typer.Option(False, "--no-images", help="추출 이미지를 저장하지 않는다"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="진행 메시지를 감춘다"),
) -> None:
    """문서를 Markdown 으로 변환한다."""
    cfg = _load(config, api, base_url, model)
    if pick_model:
        cfg.llm.model = _pick_model(cfg)
    if refine and not cfg.llm.model:
        raise _fail("--refine 에는 모델이 필요합니다. --model 또는 --pick-model 을 쓰세요.")

    files = collect_inputs(list(paths), recursive=recursive)
    if not files:
        raise _fail("변환할 파일을 찾지 못했습니다.")

    stems = output_stems(files)
    progress = None if (quiet or stdout) else lambda msg: err_console.print(f"[dim]{msg}[/]")
    failures = 0
    for path in files:
        try:
            result = convert_file(
                path,
                engine=engine,
                cfg=cfg,
                refine=refine,
                refine_model=cfg.llm.model or None,
                progress=progress,
            )
        except (BackendUnavailable, EngineNotFound, LLMError, ConfigError, FileNotFoundError) as exc:
            err_console.print(f"[red]실패[/] {path.name}: {escape(str(exc))}")
            failures += 1
            continue
        except Exception as exc:  # 엔진 내부 오류는 파일 단위로 격리한다
            err_console.print(f"[red]실패[/] {path.name}: {type(exc).__name__}: {escape(str(exc))}")
            failures += 1
            continue

        for warning in result.warnings:
            err_console.print(f"[yellow]경고[/] {path.name}: {escape(warning)}")

        if stdout:
            sys.stdout.write(result.markdown + "\n")
            continue
        written = write_result(result, out, stem=stems[path], save_images=not no_images)
        suffix = f" (+이미지 {written.image_count}개)" if written.image_count else ""
        refined = " +정제" if result.llm_refined else ""
        if not quiet:
            console.print(
                f"[green]완료[/] {path.name} → {written.markdown_path} "
                f"[dim]{result.engine}{refined} {result.elapsed:.1f}s "
                f"{len(result.markdown):,}자{suffix}[/]"
            )
    if failures:
        raise typer.Exit(code=1)


# --------------------------------------------------------------------- compare
@app.command()
def compare(
    path: Path = typer.Argument(..., help="비교에 쓸 문서 하나"),
    engine_list: str = typer.Option(
        "", "--engines", "-e", help="쉼표로 구분 (비우면 설치된 엔진 전부)"
    ),
    out: Path = typer.Option(None, "--out", "-o", help="엔진별 결과를 저장할 디렉터리"),
    judge: bool = typer.Option(False, "--judge", help="사내 모델에게 순위를 매기게 한다"),
    model: str = typer.Option(None, "--model", "-m", help="심사에 쓸 사내 모델 ID"),
    api: str = typer.Option(None, "--api", help="openai | anthropic"),
    base_url: str = typer.Option(None, "--base-url", help="사내 게이트웨이 주소"),
    config: str = typer.Option(None, "--config", "-c", help="설정파일 경로"),
    json_out: bool = typer.Option(False, "--json", help="지표를 JSON 으로 출력"),
) -> None:
    """같은 문서를 여러 엔진으로 변환해 결과를 비교한다."""
    cfg = _load(config, api, base_url, model)
    if not path.is_file():
        raise _fail(f"파일이 없습니다: {path}")

    if engine_list:
        names = [n.strip() for n in engine_list.split(",") if n.strip()]
    else:
        names = [c.name for c in engines_for(path) if not c.needs_llm]
    if not names:
        raise _fail(f"{path.suffix} 를 지원하는 설치된 엔진이 없습니다. doc2md engines 로 확인하세요.")

    markdowns: dict[str, str] = {}
    elapsed: dict[str, float] = {}
    for name in names:
        try:
            result = convert_file(path, engine=name, cfg=cfg)
        except Exception as exc:
            err_console.print(f"[red]실패[/] {name}: {type(exc).__name__}: {escape(str(exc))}")
            continue
        markdowns[name] = result.markdown
        elapsed[name] = result.elapsed
        if out:
            write_result(result, out, stem=f"{path.stem}.{name}")
    if not markdowns:
        raise _fail("성공한 엔진이 없습니다.")

    stats = summarize(markdowns, elapsed)
    if json_out:
        console.print_json(to_json({"source": str(path), "engines": stats}))
    else:
        table = Table(title=f"엔진 비교: {path.name}")
        table.add_column("엔진", style="bold")
        table.add_column("초", justify="right")
        table.add_column("글자", justify="right")
        table.add_column("제목", justify="right")
        table.add_column("목록", justify="right")
        table.add_column("표", justify="right")
        table.add_column("표 행", justify="right")
        table.add_column("표 정합", justify="right")
        table.add_column("수식", justify="right")
        table.add_column("잡음줄", justify="right")
        for name, data in stats.items():
            table.add_row(
                name,
                f"{data['elapsed']:.1f}",
                f"{data['chars']:,}",
                str(data["headings"]),
                str(data["list_items"]),
                str(data["table_blocks"]),
                str(data["table_rows"]),
                f"{data['table_health']:.2f}",
                str(data["math"]),
                str(data["noise_lines"]),
            )
        console.print(table)
        console.print(
            "[dim]글자 수가 많다고 정확한 건 아닙니다. 표 정합(열 개수 일관성)과 잡음줄을 함께 보세요.[/]"
        )
    if out:
        console.print(f"[green]결과 저장[/] {out}")

    if judge:
        _judge(cfg, markdowns)


def _judge(cfg, markdowns: dict[str, str]) -> None:
    """사내 모델에게 변환 결과 순위를 매기게 한다."""
    limit = 6000  # 모델 컨텍스트를 아끼려고 앞부분만 보낸다
    blocks = [
        f"### 엔진: {name}\n```markdown\n{md[:limit]}\n```"
        for name, md in markdowns.items()
    ]
    try:
        with LLMClient(cfg.llm) as client:
            answer = client.complete(
                JUDGE_PROMPT.format(candidates="\n\n".join(blocks)),
                system=JUDGE_SYSTEM,
            )
    except (LLMError, ConfigError) as exc:
        err_console.print(f"[yellow]심사 건너뜀[/] {escape(str(exc))}")
        return

    try:
        start, end = answer.index("{"), answer.rindex("}") + 1
        verdict = json.loads(answer[start:end])
    except (ValueError, json.JSONDecodeError):
        console.print("[yellow]모델 응답을 JSON 으로 못 읽었습니다. 원문:[/]")
        console.print(answer[:1500])
        return

    table = Table(title="사내 모델 심사 결과")
    table.add_column("순위", justify="right")
    table.add_column("엔진", style="bold")
    table.add_column("점수", justify="right")
    table.add_column("근거")
    ranking = sorted(verdict.get("ranking", []), key=lambda r: r.get("score", 0), reverse=True)
    for index, row in enumerate(ranking, start=1):
        table.add_row(str(index), str(row.get("engine", "")), str(row.get("score", "")), str(row.get("reason", "")))
    console.print(table)
    if verdict.get("best"):
        console.print(f"[green]추천 엔진[/] {verdict['best']}")


# ---------------------------------------------------------------------- config
config_app = typer.Typer(no_args_is_help=True, help="설정파일 관리")
app.add_typer(config_app, name="config")


@config_app.command("init")
def config_init(
    path: Path = typer.Option(None, "--path", "-p", help=f"기본값: {USER_CONFIG_PATH}"),
    force: bool = typer.Option(False, "--force", help="기존 파일을 덮어쓴다"),
) -> None:
    """설정파일 템플릿을 만든다."""
    target = path or USER_CONFIG_PATH
    if target.exists() and not force:
        raise _fail(f"이미 있습니다: {target} (덮어쓰려면 --force)")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(SAMPLE_CONFIG, encoding="utf-8")
    console.print(f"[green]생성[/] {target}\n  사내 게이트웨이 주소·모델 ID 를 채워 넣으세요.")


@config_app.command("show")
def config_show(
    config: str = typer.Option(None, "--config", "-c", help="설정파일 경로"),
) -> None:
    """현재 적용되는 설정을 보여 준다 (API 키는 가린다)."""
    cfg = _load(config, None, None, None)
    table = Table(title="현재 설정")
    table.add_column("항목", style="bold")
    table.add_column("값")
    table.add_row("설정파일", str(cfg.source_path or "(없음 — 환경변수/기본값만)"))
    table.add_row("default_engine", cfg.default_engine)
    table.add_row("llm.api", cfg.llm.api)
    table.add_row("llm.base_url", cfg.llm.base_url or "[dim](미설정)[/]")
    table.add_row("llm.api_key", "설정됨" if cfg.llm.api_key else "[dim](미설정)[/]")
    table.add_row("llm.model", cfg.llm.model or "[dim](미설정)[/]")
    table.add_row("llm.vision_model", cfg.llm.effective_vision_model or "[dim](미설정)[/]")
    for engine_name, options in cfg.engine_options.items():
        table.add_row(f"engines.{engine_name}", str(options))
    console.print(table)


@app.command()
def version() -> None:
    """버전 출력."""
    console.print(f"doc2md {__version__}")


if __name__ == "__main__":  # pragma: no cover
    app()
