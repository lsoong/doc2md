"""doc2md 커맨드라인.

  doc2md engines                      설치된 엔진과 지원 포맷 확인
  doc2md profiles                     등록해 둔 사내 모델 목록
  doc2md licenses                     엔진 라이선스 점검
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
from .config import (
    SAMPLE_CONFIG,
    USER_CONFIG_PATH,
    ConfigError,
    api_key_status,
    ensure_self_hosted,
    is_external_llm_host,
    load_config,
    override_llm,
    ready_llm,
    set_engine_options,
)
from .llm import JUDGE_PROMPT, JUDGE_SYSTEM, LLMClient, LLMError
from .metrics import summarize, to_json
from .pipeline import collect_inputs, convert_file, output_stems, write_result

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "PDF·Word·PPT·Excel 문서를 Markdown 으로 변환합니다 "
        "(멀티 엔진 + 사내 자체 서빙 LLM 연동)."
    ),
)
console = Console()
err_console = Console(stderr=True)


def _fail(message: str) -> "typer.Exit":
    err_console.print(f"[bold red]오류[/] {escape(message)}")
    return typer.Exit(code=1)


PROFILE_OPTION = typer.Option(
    None, "--profile", "-P", help="쓸 사내 모델 프로필 (doc2md profiles 로 목록 확인)"
)


def _load(
    config: str | None,
    base_url: str | None,
    model: str | None,
    vision_model: str | None = None,
    profile: str | None = None,
):
    try:
        cfg = load_config(config, profile=profile)
    except ConfigError as exc:
        raise _fail(str(exc)) from exc
    return override_llm(cfg, base_url=base_url, model=model, vision_model=vision_model)


def _apply_engine_llm(
    cfg,
    *,
    engine: str,
    engine_llm: bool,
    llm_mode: str | None,
    llm_model: str | None,
    llm_url: str | None,
) -> None:
    """--engine-llm 계열 인자를 엔진 옵션으로 옮긴다."""
    set_engine_options(
        cfg,
        engine,
        {"llm_mode": llm_mode, "llm_model": llm_model, "server_url": llm_url},
        all_engines=list(ENGINES),
    )
    if not engine_llm:
        return

    if engine not in ("", "auto"):
        cls = ENGINES.get(engine)
        if cls is not None and not cls.llm_hooks:
            raise _fail(
                f"{engine} 엔진에는 내장 LLM 연결이 없습니다. "
                "--refine 을 쓰거나 doc2md engines --llm 으로 지원 엔진을 확인하세요."
            )
        names = [engine]
    else:
        # auto 는 어느 엔진이 뽑힐지 모른다. 사내 게이트웨이에 바로 붙는 엔진만 켜고,
        # 별도 추론 서버가 필요한 엔진(mineru)은 --llm-url 을 준 경우에만 켠다.
        names = [
            name
            for name, cls in ENGINES.items()
            if cls.llm_hooks and ((hook := cls.default_hook()) and (hook.uses_gateway or llm_url))
        ]
    for name in names:
        cfg.engine_options.setdefault(name, {})["use_llm"] = True


# --------------------------------------------------------------------- engines
@app.command()
def engines(
    fmt: str = typer.Option("", "--format", "-f", help="이 확장자를 지원하는 엔진만 (예: pdf)"),
    llm: bool = typer.Option(False, "--llm", help="엔진별 LLM 연결 방식을 자세히 본다"),
) -> None:
    """설치된 변환 엔진과 지원 포맷을 보여 준다."""
    classes = list(ENGINE_CLASSES)
    if fmt:
        suffix = fmt if fmt.startswith(".") else f".{fmt}"
        classes = [c for c in classes if suffix.lower() in c.extensions]
        if not classes:
            raise _fail(f"{suffix} 를 지원하는 엔진이 없습니다.")
    ordered = sorted(classes, key=lambda c: c.priority, reverse=True)

    if llm:
        table = Table(title="엔진 내장 LLM 연결 (--engine-llm 으로 켠다)")
        table.add_column("엔진", style="bold")
        table.add_column("--llm-mode")
        table.add_column("기본", justify="center")
        table.add_column("붙는 대상")
        table.add_column("하는 일")
        for cls in ordered:
            if not cls.llm_hooks:
                table.add_row(cls.name, "[dim]없음[/]", "", "", "[dim]--refine(후처리 정제)으로 대체됩니다[/]")
                continue
            for hook in cls.llm_hooks:
                table.add_row(
                    cls.name,
                    hook.mode,
                    "●" if hook.default else "",
                    "사내 게이트웨이" if hook.uses_gateway else "[yellow]별도 추론 서버[/]",
                    escape(hook.summary),
                )
        console.print(table)
        console.print(
            "모델은 --llm-model 로 고릅니다. 생략하면 vision_model(없으면 model)을 씁니다.\n"
            "어느 엔진에도 붙지 않는 일반 정제는 --refine 입니다.\n"
            "[dim]연결 대상은 사내 자체 서빙 모델뿐입니다 — 과금되는 외부 API 주소는 거부됩니다.[/]"
        )
        return

    table = Table(title="doc2md 변환 엔진 (우선순위 높은 순)")
    table.add_column("엔진", style="bold")
    table.add_column("이름")
    table.add_column("상태")
    table.add_column("우선순위", justify="right")
    table.add_column("내장 LLM")
    table.add_column("지원 포맷")
    for cls in ordered:
        available = cls.is_available()
        status = "[green]설치됨[/]" if available else f"[yellow]미설치[/] ({escape(cls.install_hint)})"
        if cls.needs_llm:
            status += " [cyan]+사내모델 필요[/]"
        hooks = "/".join(h.mode for h in cls.llm_hooks) or "[dim]—[/]"
        table.add_row(
            cls.name,
            cls.title,
            status,
            str(cls.priority),
            hooks,
            " ".join(e.lstrip(".") for e in cls.extensions),
        )
    console.print(table)
    console.print(
        "auto 선택 시 위 순서대로 설치된 첫 엔진을 씁니다 (vlm 은 명시할 때만).\n"
        "내장 LLM 연결은 --engine-llm 으로 켜고, 자세한 설명은 doc2md engines --llm."
    )


# -------------------------------------------------------------------- licenses
@app.command()
def licenses(
    installed_only: bool = typer.Option(
        False, "--installed", help="지금 설치된 엔진만 본다"
    ),
) -> None:
    """엔진별 라이선스와 사용 조건을 보여 준다 (자세한 검토는 docs/licenses.md)."""
    table = Table(title="엔진 라이선스 (doc2md 본체는 MIT)")
    table.add_column("엔진", style="bold")
    table.add_column("설치", justify="center")
    table.add_column("코드")
    table.add_column("모델 가중치")
    table.add_column("판정")
    ordered = sorted(ENGINE_CLASSES, key=lambda c: c.priority, reverse=True)
    for cls in ordered:
        available = cls.is_available()
        if installed_only and not available:
            continue
        table.add_row(
            cls.name,
            "[green]●[/]" if available else "[dim]—[/]",
            ("[yellow]" + cls.license + "[/]") if cls.license_copyleft else cls.license,
            cls.weights_license or "[dim]코드와 동일[/]",
            cls.license_verdict
            if cls.license_verdict == "제약 없음"
            else f"[yellow]{cls.license_verdict}[/]",
        )
    console.print(table)
    for cls in ordered:
        if installed_only and not cls.is_available():
            continue
        if cls.license_note:
            console.print(f"[bold]{cls.name}[/] — {escape(cls.license_note)}")
            if cls.license_url:
                console.print(f"  [dim]{cls.license_url}[/]")
    console.print(
        "\n[bold]요약[/] 사내에서 내부 문서를 변환하는 용도로는 여섯 엔진 모두 그대로 쓸 수 있다.\n"
        "외부 배포·사외 서비스 제공은 [yellow]노란색 표시 엔진[/]을 빼거나 별도 검토가 필요하다.\n"
        "[dim]자세한 근거와 판단은 docs/licenses.md.[/]"
    )


# ---------------------------------------------------------------------- models
@app.command()
def models(
    config: str = typer.Option(None, "--config", "-c", help="설정파일 경로"),
    profile: str = PROFILE_OPTION,
    base_url: str = typer.Option(
        None, "--base-url", help="사내 게이트웨이 주소 (OpenAI 호환, 자체 서빙)"
    ),
) -> None:
    """사내 게이트웨이가 제공하는 모델 목록을 조회한다."""
    cfg = _load(config, base_url, None, profile=profile)
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
    title = f"사내 모델 ({cfg.llm.base_url})"
    if cfg.active_profile:
        title += f" — 프로필 {cfg.active_profile}"
    table = Table(title=title)
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


# -------------------------------------------------------------------- profiles
@app.command()
def profiles(
    config: str = typer.Option(None, "--config", "-c", help="설정파일 경로"),
    check: bool = typer.Option(False, "--check", help="프로필마다 실제로 접속해 본다"),
) -> None:
    """설정파일에 등록해 둔 사내 모델 프로필을 보여 준다."""
    cfg = _load(config, None, None)
    if not cfg.profiles:
        console.print(
            "[yellow]등록된 프로필이 없습니다.[/]\n"
            f"  설정파일에 {escape('[llm.profiles.<이름>]')} 을 추가하면 "
            "--profile 로 골라 쓸 수 있습니다.\n"
            f"  템플릿: doc2md config init  (기본 위치 {USER_CONFIG_PATH})"
        )
        if cfg.llm.base_url:
            console.print(
                f"  현재는 {escape('[llm]')} 하나만 씁니다: {cfg.llm.base_url} / {cfg.llm.model}"
            )
        return

    table = Table(title=f"사내 모델 프로필 ({cfg.source_path})")
    table.add_column("", justify="center")
    table.add_column("이름", style="bold")
    table.add_column("설명")
    table.add_column("base_url")
    table.add_column("model")
    table.add_column("vision_model")
    table.add_column("API 키")
    if check:
        table.add_column("연결")
    for name, llm in cfg.profiles.items():
        mark = "●" if name == cfg.active_profile else ""
        row = [
            mark,
            name,
            llm.description or "[dim]—[/]",
            llm.base_url or "[dim](미설정)[/]",
            llm.model or "[dim](미설정)[/]",
            llm.effective_vision_model or "[dim](미설정)[/]",
            api_key_status(llm),
        ]
        if check:
            row.append(_probe(llm))
        table.add_row(*row)
    console.print(table)
    console.print(
        "● = 지금 선택된 프로필. 고르는 순서: --profile > $DOC2MD_PROFILE > default_profile"
        f"{' (' + cfg.default_profile + ')' if cfg.default_profile else ''}.\n"
        "[dim]모든 프로필은 사내 자체 서빙 주소만 허용됩니다 — 과금되는 외부 API 는 거부됩니다.[/]"
    )


def _probe(llm) -> str:
    """프로필 하나에 실제로 접속해 모델 목록을 받아 본다."""
    try:
        with LLMClient(ready_llm(llm)) as client:
            found = client.list_models()
    except (LLMError, ConfigError) as exc:
        return f"[red]실패[/] {escape(str(exc).splitlines()[0][:60])}"
    if llm.model and found and llm.model not in found:
        return f"[yellow]연결됨, model 없음[/] ({len(found)}개 제공)"
    return f"[green]연결됨[/] ({len(found)}개)" if found else "[green]연결됨[/]"


def _pick_model(cfg, *, also_vision: bool = False) -> None:
    """대화식으로 모델을 고른다. cfg.llm 을 직접 고쳐 준다."""
    try:
        with LLMClient(cfg.llm) as client:
            found = client.list_models()
    except (LLMError, ConfigError) as exc:
        raise _fail(str(exc)) from exc
    if not found:
        raise _fail("고를 수 있는 모델이 없습니다. --model 로 직접 지정하세요.")
    for index, name in enumerate(found, start=1):
        console.print(f"  [bold]{index:2}[/] {name}")

    def ask(label: str, default: str) -> str:
        choice = typer.prompt(label, default=default)
        try:
            return found[int(choice) - 1]
        except (ValueError, IndexError):
            raise _fail(f"잘못된 선택: {choice}") from None

    cfg.llm.model = ask("사용할 모델 번호", "1")
    if also_vision:
        cfg.llm.vision_model = ask("이미지를 읽을 비전 모델 번호 (엔진 내장 LLM·vlm 용)", "1")


# --------------------------------------------------------------------- convert
@app.command()
def convert(
    paths: list[Path] = typer.Argument(..., help="변환할 파일 또는 디렉터리"),
    engine: str = typer.Option("auto", "--engine", "-e", help=f"{'|'.join(ENGINES)}|auto"),
    out: Path = typer.Option(Path("."), "--out", "-o", help="출력 디렉터리"),
    stdout: bool = typer.Option(False, "--stdout", help="파일 대신 표준출력으로"),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="디렉터리를 재귀 탐색"),
    refine: bool = typer.Option(False, "--refine", help="변환이 끝난 Markdown 을 사내 모델로 다듬는다"),
    engine_llm: bool = typer.Option(
        False, "--engine-llm", "-L", help="엔진에 내장된 LLM 연결을 켠다 (doc2md engines --llm)"
    ),
    llm_mode: str = typer.Option(None, "--llm-mode", help="엔진이 여러 연결 방식을 가질 때 고른다"),
    llm_model: str = typer.Option(None, "--llm-model", help="엔진 내장 LLM 이 쓸 모델 ID"),
    llm_url: str = typer.Option(None, "--llm-url", help="엔진이 별도 추론 서버를 쓸 때의 주소 (mineru)"),
    pick_model: bool = typer.Option(False, "--pick-model", help="모델을 대화식으로 고른다"),
    profile: str = PROFILE_OPTION,
    model: str = typer.Option(None, "--model", "-m", help="사내 모델 ID (정제용 기본 모델)"),
    vision_model: str = typer.Option(None, "--vision-model", help="이미지를 읽을 비전 모델 ID"),
    base_url: str = typer.Option(
        None, "--base-url", help="사내 게이트웨이 주소 (OpenAI 호환, 자체 서빙)"
    ),
    config: str = typer.Option(None, "--config", "-c", help="설정파일 경로"),
    no_images: bool = typer.Option(False, "--no-images", help="추출 이미지를 저장하지 않는다"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="진행 메시지를 감춘다"),
) -> None:
    """문서를 Markdown 으로 변환한다."""
    cfg = _load(config, base_url, model, vision_model, profile=profile)
    if not quiet and cfg.active_profile and (refine or engine_llm or engine == "vlm"):
        err_console.print(f"[dim]사내 모델 프로필: {cfg.active_profile} ({cfg.llm.model})[/]")
    if pick_model:
        _pick_model(cfg, also_vision=engine_llm or engine == "vlm")
    if refine and not cfg.llm.model:
        raise _fail("--refine 에는 모델이 필요합니다. --model 또는 --pick-model 을 쓰세요.")
    if refine or engine_llm:
        # 변환을 다 돌린 뒤가 아니라 시작 전에 막는다.
        try:
            ensure_self_hosted(cfg.llm.base_url, "llm.base_url")
        except ConfigError as exc:
            raise _fail(str(exc)) from exc
    _apply_engine_llm(
        cfg,
        engine=engine,
        engine_llm=engine_llm,
        llm_mode=llm_mode,
        llm_model=llm_model,
        llm_url=llm_url,
    )

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
        refined = f" +LLM:{result.engine_llm}" if result.engine_llm else ""
        refined += " +정제" if result.llm_refined else ""
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
    engine_llm: bool = typer.Option(
        False, "--engine-llm", "-L", help="각 엔진의 내장 LLM 연결을 켜고 비교한다"
    ),
    llm_mode: str = typer.Option(None, "--llm-mode", help="엔진 내장 LLM 의 연결 방식"),
    llm_model: str = typer.Option(None, "--llm-model", help="엔진 내장 LLM 이 쓸 모델 ID"),
    profile: str = PROFILE_OPTION,
    model: str = typer.Option(None, "--model", "-m", help="심사에 쓸 사내 모델 ID"),
    vision_model: str = typer.Option(None, "--vision-model", help="이미지를 읽을 비전 모델 ID"),
    base_url: str = typer.Option(
        None, "--base-url", help="사내 게이트웨이 주소 (OpenAI 호환, 자체 서빙)"
    ),
    config: str = typer.Option(None, "--config", "-c", help="설정파일 경로"),
    json_out: bool = typer.Option(False, "--json", help="지표를 JSON 으로 출력"),
) -> None:
    """같은 문서를 여러 엔진으로 변환해 결과를 비교한다."""
    cfg = _load(config, base_url, model, vision_model, profile=profile)
    if not path.is_file():
        raise _fail(f"파일이 없습니다: {path}")

    if engine_list:
        names = [n.strip() for n in engine_list.split(",") if n.strip()]
    else:
        names = [c.name for c in engines_for(path) if not c.needs_llm]
    if not names:
        raise _fail(f"{path.suffix} 를 지원하는 설치된 엔진이 없습니다. doc2md engines 로 확인하세요.")

    if engine_llm:
        # 훅이 없는 엔진은 그대로 두고, 있는 엔진만 켠다 (비교 대상이 사라지지 않게)
        for name in names:
            cls = ENGINES.get(name)
            if cls is not None and cls.llm_hooks:
                _apply_engine_llm(
                    cfg,
                    engine=name,
                    engine_llm=True,
                    llm_mode=llm_mode,
                    llm_model=llm_model,
                    llm_url=None,
                )

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
    profile: str = PROFILE_OPTION,
) -> None:
    """현재 적용되는 설정을 보여 준다 (API 키는 가린다)."""
    cfg = _load(config, None, None, profile=profile)
    table = Table(title="현재 설정")
    table.add_column("항목", style="bold")
    table.add_column("값")
    table.add_row("설정파일", str(cfg.source_path or "(없음 — 환경변수/기본값만)"))
    table.add_row("default_engine", cfg.default_engine)
    table.add_row(
        "프로필",
        f"{cfg.active_profile} (등록 {len(cfg.profiles)}개)"
        if cfg.active_profile
        else f"[dim](프로필 없이 {escape('[llm]')} 사용)[/]",
    )
    table.add_row("llm.base_url", cfg.llm.base_url or "[dim](미설정)[/]")
    table.add_row("llm.api_key", "설정됨" if cfg.llm.api_key else "[dim](미설정)[/]")
    table.add_row("llm.model", cfg.llm.model or "[dim](미설정)[/]")
    table.add_row("llm.vision_model", cfg.llm.effective_vision_model or "[dim](미설정)[/]")
    table.add_row(
        "엔드포인트 검사",
        "[red]외부 상용 API — 차단됨[/]"
        if cfg.llm.base_url and is_external_llm_host(cfg.llm.base_url)
        else "[green]사내 자체 서빙[/]",
    )
    for engine_name, options in cfg.engine_options.items():
        table.add_row(f"engines.{engine_name}", str(options))
    console.print(table)


@app.command()
def version() -> None:
    """버전 출력."""
    console.print(f"doc2md {__version__}")


if __name__ == "__main__":  # pragma: no cover
    app()
