"""doc2md 단위 테스트.

실행: .venv/bin/python -m pytest tests -q
샘플 문서가 없으면 tests/make_samples.py 로 먼저 만든다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from doc2md.backends import ENGINES, engines_for, pick_auto  # noqa: E402
from doc2md.config import Config, LLMConfig, load_config  # noqa: E402
from doc2md.llm import LLMClient  # noqa: E402
from doc2md.metrics import measure, table_health  # noqa: E402
from doc2md.pipeline import (  # noqa: E402
    collect_inputs,
    convert_file,
    output_stems,
    refine_markdown,
    split_markdown,
)
from stub_gateway import MODELS, StubGateway  # noqa: E402

SAMPLES = Path(__file__).parent / "samples"


def gateway_config(base_url: str, api: str = "openai") -> Config:
    return Config(llm=LLMConfig(api=api, base_url=base_url, model="corp-llm-32b", timeout=10))


# ----------------------------------------------------------------- 지표
def test_measure_counts_structure():
    md = "# 제목\n\n- 항목\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\n$x^2$\n\n12\n"
    m = measure(md)
    assert m.headings == 1
    assert m.list_items == 1
    assert m.table_blocks == 1
    assert m.table_rows == 3
    assert m.math == 1
    assert m.noise_lines == 1  # 쪽번호처럼 보이는 "12"


def test_table_health_detects_broken_table():
    good = "| a | b |\n| --- | --- |\n| 1 | 2 |"
    broken = "| a | b |\n| --- | --- |\n| 1 | 2 | 3 |\n| 4 |"
    assert table_health(good) == 1.0
    assert table_health(broken) < 1.0
    assert table_health("표 없음") == 1.0


# ----------------------------------------------------------------- 청크 분할
def test_split_markdown_keeps_code_fence_intact():
    md = "문단 하나\n\n```python\nprint(1)\n\nprint(2)\n```\n\n마지막 문단"
    chunks = split_markdown(md, chunk_chars=10)
    fenced = [c for c in chunks if "```" in c]
    assert len(fenced) == 1
    assert fenced[0].count("```") == 2  # 코드블록이 두 조각으로 갈리지 않았다


def test_split_markdown_respects_chunk_size():
    md = "\n\n".join(f"문단 {i} " + "가" * 100 for i in range(20))
    chunks = split_markdown(md, chunk_chars=500)
    assert len(chunks) > 1
    assert all(len(c) < 1200 for c in chunks)


# ----------------------------------------------------------------- 출력 이름
def test_output_stems_disambiguates_same_name():
    files = [Path("a/보고서.pdf"), Path("a/보고서.docx"), Path("a/기타.xlsx")]
    stems = output_stems(files)
    assert stems[Path("a/보고서.pdf")] == "보고서.pdf"
    assert stems[Path("a/보고서.docx")] == "보고서.docx"
    assert stems[Path("a/기타.xlsx")] == "기타"  # 겹치지 않으면 그대로


def test_output_stems_handles_cross_directory_collision():
    files = [Path("a/보고서.pdf"), Path("b/보고서.pdf")]
    stems = output_stems(files)
    assert len(set(stems.values())) == 2


# ----------------------------------------------------------------- 엔진 선택
@pytest.mark.skipif(not SAMPLES.exists(), reason="샘플 문서 없음")
def test_pick_auto_prefers_highest_priority_installed():
    backend = pick_auto(SAMPLES / "sample.pdf")
    candidates = [c for c in engines_for(SAMPLES / "sample.pdf") if not c.needs_llm]
    assert backend.name == candidates[0].name
    assert not backend.needs_llm  # LLM 과금 엔진은 자동 선택하지 않는다


def test_collect_inputs_filters_unknown_extensions(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "b.bin").write_bytes(b"\x00")
    found = collect_inputs([tmp_path])
    assert [p.name for p in found] == ["a.pdf"]


def test_engine_registry_names_are_unique():
    assert len(ENGINES) == 6
    assert "docling" in ENGINES and "vlm" in ENGINES


# ----------------------------------------------------------------- 사내 모델 연동
def test_list_models_from_gateway():
    with StubGateway() as gw:
        with LLMClient(gateway_config(gw.base_url).llm) as client:
            assert client.list_models() == sorted(MODELS)


def test_refine_sends_chunks_and_model():
    with StubGateway() as gw:
        cfg = gateway_config(gw.base_url)
        out = refine_markdown("# 제목\n\n본문 한 줄", cfg=cfg, chunk_chars=6000)
        assert out.startswith("## 정제됨")
        sent = gw.requests[-1]["payload"]
        assert sent["model"] == "corp-llm-32b"
        assert sent["messages"][0]["role"] == "system"


def test_refine_uses_explicit_model_override():
    with StubGateway() as gw:
        cfg = gateway_config(gw.base_url)
        refine_markdown("본문", cfg=cfg, model="corp-llm-8b")
        assert gw.requests[-1]["payload"]["model"] == "corp-llm-8b"


def test_anthropic_dialect_uses_messages_endpoint():
    with StubGateway() as gw:
        cfg = gateway_config(gw.base_url, api="anthropic")
        cfg.llm.api_key = "k"
        out = refine_markdown("본문", cfg=cfg)
        assert "anthropic" in out
        req = gw.requests[-1]
        assert req["path"].endswith("/v1/messages")
        assert req["headers"]["x-api-key"] == "k"
        assert req["headers"]["anthropic-version"] == "2023-06-01"


def test_api_key_goes_in_bearer_header_for_openai():
    with StubGateway() as gw:
        cfg = gateway_config(gw.base_url)
        cfg.llm.api_key = "secret-key"
        refine_markdown("본문", cfg=cfg)
        assert gw.requests[-1]["headers"]["Authorization"] == "Bearer secret-key"


@pytest.mark.skipif(not SAMPLES.exists(), reason="샘플 문서 없음")
def test_vlm_backend_sends_page_images():
    pytest.importorskip("pymupdf")
    with StubGateway() as gw:
        cfg = gateway_config(gw.base_url)
        cfg.llm.vision_model = "corp-vl-32b"
        cfg.engine_options["vlm"] = {"dpi": 72}
        result = convert_file(SAMPLES / "sample.pdf", engine="vlm", cfg=cfg)
        assert "비전 변환 결과" in result.markdown
        payload = gw.requests[-1]["payload"]
        assert payload["model"] == "corp-vl-32b"
        parts = payload["messages"][-1]["content"]
        images = [p for p in parts if p.get("type") == "image_url"]
        assert len(images) == 1
        assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.skipif(not SAMPLES.exists(), reason="샘플 문서 없음")
def test_vlm_respects_max_pages():
    pytest.importorskip("pymupdf")
    with StubGateway() as gw:
        cfg = gateway_config(gw.base_url)
        cfg.engine_options["vlm"] = {"dpi": 72, "max_pages": 1}
        result = convert_file(SAMPLES / "sample.pdf", engine="vlm", cfg=cfg)
        assert result.pages == 1


# ----------------------------------------------------------------- 설정
def test_config_file_and_env_override(tmp_path, monkeypatch):
    path = tmp_path / "doc2md.toml"
    path.write_text(
        '[llm]\nbase_url = "https://gw.example/v1"\nmodel = "a"\napi_key = "env:MY_KEY"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MY_KEY", "from-env")
    monkeypatch.setenv("DOC2MD_MODEL", "b")
    cfg = load_config(path)
    assert cfg.llm.api_key == "from-env"
    assert cfg.llm.model == "b"  # 환경변수가 설정파일을 이긴다
    assert cfg.llm.effective_vision_model == "b"  # vision_model 미지정이면 model 사용


def test_config_rejects_unknown_key(tmp_path):
    from doc2md.config import ConfigError

    path = tmp_path / "doc2md.toml"
    path.write_text('[llm]\nnope = 1\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_llm_client_requires_base_url_and_model():
    from doc2md.config import ConfigError

    with pytest.raises(ConfigError):
        LLMClient(LLMConfig())


# ----------------------------------------------------------------- 실제 변환
@pytest.mark.skipif(not SAMPLES.exists(), reason="샘플 문서 없음")
@pytest.mark.parametrize("name", ["sample.docx", "sample.pptx", "sample.xlsx"])
def test_office_formats_roundtrip(name):
    result = convert_file(SAMPLES / name, engine="markitdown")
    assert "MinerU" in result.markdown  # 표 안의 값이 살아 있다
    assert result.elapsed > 0


@pytest.mark.skipif(not SAMPLES.exists(), reason="샘플 문서 없음")
def test_convert_with_refine_marks_result():
    with StubGateway() as gw:
        cfg = gateway_config(gw.base_url)
        result = convert_file(SAMPLES / "sample.xlsx", engine="markitdown", cfg=cfg, refine=True)
        assert result.llm_refined is True
        assert result.markdown.startswith("## 정제됨")
