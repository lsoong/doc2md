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
from doc2md.backends.docling_backend import (  # noqa: E402
    chat_endpoint,
    inject_picture_descriptions,
)
from doc2md.backends.marker_backend import marker_llm_config  # noqa: E402
from doc2md.config import (  # noqa: E402
    Config,
    ConfigError,
    LLMConfig,
    ensure_self_hosted,
    is_external_llm_host,
    load_config,
)
from doc2md.llm import LLMClient, OpenAICompatClient  # noqa: E402
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


def gateway_config(base_url: str) -> Config:
    return Config(llm=LLMConfig(base_url=base_url, model="corp-llm-32b", timeout=10))


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


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "https://api.anthropic.com",
        "https://my-deploy.openai.azure.com/openai",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "https://openrouter.ai/api/v1",
        "https://bedrock-runtime.us-east-1.amazonaws.com",
        "api.mistral.ai/v1",
    ],
)
def test_external_paid_endpoints_are_blocked(url):
    """과금되는 외부 모델 API 는 호출 전에 막는다 (사내 자체 서빙 모델 전용)."""
    assert is_external_llm_host(url)
    cfg = Config(llm=LLMConfig(base_url=url, model="gpt-4o"))
    with pytest.raises(ConfigError, match="외부 상용 LLM API"):
        refine_markdown("본문", cfg=cfg)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/v1",
        "http://vllm.internal:8000/v1",
        "https://llm-gw.example.corp/v1",
        "http://ollama.사내:11434/v1",
    ],
)
def test_self_hosted_endpoints_pass_the_guard(url):
    assert not is_external_llm_host(url)
    ensure_self_hosted(url, "테스트")


def test_engine_hook_also_blocks_external_endpoint():
    """엔진이 직접 HTTP 를 치는 경로(docling·marker·markitdown)도 같은 검사를 받는다."""
    cfg = engine_llm_config("https://api.openai.com/v1", "markitdown")
    with pytest.raises(ConfigError, match="외부 상용 LLM API"):
        convert_file(SAMPLES / "sample.png", engine="markitdown", cfg=cfg)


def test_removed_api_key_in_config_file_explains_itself(tmp_path):
    path = tmp_path / "doc2md.toml"
    path.write_text('[llm]\napi = "anthropic"\nbase_url = "https://gw/v1"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="OpenAI 호환"):
        load_config(path)


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


# ------------------------------------------------- 엔진 내장 LLM 연결 (SYA-31 추가분)
def engine_llm_config(base_url: str, engine: str, **options) -> Config:
    """엔진 내장 LLM 연결을 켠 설정."""
    cfg = Config(
        llm=LLMConfig(
            base_url=base_url,
            model="corp-llm-32b",
            vision_model="corp-vl-32b",
            timeout=30,
        )
    )
    cfg.engine_options[engine] = {"use_llm": True, **options}
    return cfg


def test_shim_client_speaks_openai_chat_api():
    with StubGateway() as gw:
        with LLMClient(gateway_config(gw.base_url).llm) as client:
            shim = OpenAICompatClient(client)
            answer = shim.chat.completions.create(
                model="corp-vl-32b",
                messages=[{"role": "user", "content": "본문\n두 번째 줄"}],
            )
        assert answer.choices[0].message.content.startswith("## 정제됨")
        assert gw.requests[-1]["payload"]["model"] == "corp-vl-32b"


@pytest.mark.skipif(not (SAMPLES / "sample.png").exists(), reason="이미지 샘플 없음")
def test_markitdown_caption_hook_sends_image_to_vision_model():
    pytest.importorskip("markitdown")
    with StubGateway() as gw:
        cfg = engine_llm_config(gw.base_url, "markitdown")
        result = convert_file(SAMPLES / "sample.png", engine="markitdown", cfg=cfg)
        assert result.engine_llm == "caption"
        assert "비전 변환 결과" in result.markdown
        payload = gw.requests[-1]["payload"]
        assert payload["model"] == "corp-vl-32b"  # 비전 훅은 vision_model 을 쓴다
        parts = payload["messages"][-1]["content"]
        assert any(p.get("type") == "image_url" for p in parts)


@pytest.mark.skipif(not (SAMPLES / "sample.png").exists(), reason="이미지 샘플 없음")
def test_markitdown_without_engine_llm_does_not_call_gateway():
    pytest.importorskip("markitdown")
    with StubGateway() as gw:
        cfg = gateway_config(gw.base_url)
        result = convert_file(SAMPLES / "sample.png", engine="markitdown", cfg=cfg)
        assert result.engine_llm == ""
        assert gw.requests == []


def test_engine_llm_model_option_overrides_role_default():
    with StubGateway() as gw:
        cfg = engine_llm_config(gw.base_url, "markitdown", llm_model="corp-llm-8b")
        convert_file(SAMPLES / "sample.png", engine="markitdown", cfg=cfg)
        assert gw.requests[-1]["payload"]["model"] == "corp-llm-8b"


def test_unknown_llm_mode_is_rejected():
    cfg = engine_llm_config("http://127.0.0.1:1", "markitdown", llm_mode="없는모드")
    with pytest.raises(ConfigError, match="llm_mode"):
        convert_file(SAMPLES / "sample.png", engine="markitdown", cfg=cfg)


@pytest.mark.parametrize(
    "service",
    [
        "marker.services.claude.ClaudeService",
        "marker.services.gemini.GoogleGeminiService",
        "marker.services.vertex.GoogleVertexService",
        "marker.services.azure_openai.AzureOpenAIService",
    ],
)
def test_marker_rejects_commercial_llm_services(service):
    """marker 에 딸려 오는 상용 API 서비스는 쓰지 못하게 막는다."""
    with pytest.raises(ConfigError, match="사내 게이트웨이용만"):
        marker_llm_config(
            base_url="http://vllm.internal:8000/v1",
            model="qwen3-vl-32b",
            api_key="",
            service=service,
        )


def test_engine_without_hook_falls_back_to_refine():
    pytest.importorskip("pymupdf4llm")
    with StubGateway() as gw:
        cfg = Config(llm=LLMConfig(base_url=gw.base_url, model="corp-llm-32b", timeout=30))
        cfg.engine_options["pymupdf4llm"] = {"use_llm": True}
        result = convert_file(SAMPLES / "sample.pdf", engine="pymupdf4llm", cfg=cfg)
        assert result.engine_llm == ""
        assert result.llm_refined is True  # --refine 으로 대체됐다
        assert any("내장 LLM 연결이 없어" in w for w in result.warnings)


@pytest.mark.skipif(not SAMPLES.exists(), reason="샘플 문서 없음")
def test_docling_vlm_hook_sends_page_image_to_gateway():
    pytest.importorskip("docling")
    with StubGateway() as gw:
        cfg = engine_llm_config(gw.base_url, "docling", llm_mode="vlm", scale=1.0)
        cfg.llm.api_key = "corp-key"
        result = convert_file(SAMPLES / "sample.pdf", engine="docling", cfg=cfg)
        assert result.engine_llm == "vlm"
        assert "비전 변환 결과" in result.markdown
        req = gw.requests[-1]
        assert req["payload"]["model"] == "corp-vl-32b"
        assert req["headers"]["Authorization"] == "Bearer corp-key"
        assert any(
            p.get("type") == "image_url" for p in req["payload"]["messages"][-1]["content"]
        )


@pytest.mark.skipif(
    not (SAMPLES / "sample-image.pdf").exists(), reason="그림 포함 샘플 없음"
)
def test_docling_picture_hook_describes_only_pictures():
    pytest.importorskip("docling")
    with StubGateway() as gw:
        cfg = engine_llm_config(gw.base_url, "docling", llm_mode="picture")
        result = convert_file(SAMPLES / "sample-image.pdf", engine="docling", cfg=cfg)
        assert result.engine_llm == "picture"
        # 본문은 docling 이 읽고(원문의 줄바꿈 없는 공백까지 그대로), 그림 자리에만
        # 모델 설명이 들어간다
        body = result.markdown.replace("\xa0", " ")
        assert "그림이 들어간 문서" in body
        assert "비전 변환 결과" in body  # 스텁 모델이 붙인 그림 설명
        assert "<!-- image -->" not in result.markdown  # 자리표시자가 대체됐다
        assert result.markdown.count("비전 변환 결과") == 2  # alt 텍스트 + 설명, 중복 없음
        assert len(gw.requests) == 1  # 페이지 전체가 아니라 그림 한 장만 보냈다


def test_inject_picture_descriptions_keeps_placeholder_when_empty():
    class Meta:
        def __init__(self, text):
            self.description = type("D", (), {"text": text})() if text else None

    class Picture:
        def __init__(self, text):
            self.meta = Meta(text)

    md = "앞\n\n<!-- image -->\n\n중간\n\n<!-- image -->\n\n뒤"
    doc = type("Doc", (), {"pictures": [Picture("차트"), Picture("")]})()
    out, described = inject_picture_descriptions(md, doc)
    assert described == 1
    assert "**그림 설명(사내 모델):**\n\n차트" in out
    assert out.count("<!-- image -->") == 1  # 설명이 없는 그림은 그대로 둔다


def test_inject_picture_descriptions_does_not_duplicate_existing_text():
    class Picture:
        def __init__(self, text):
            self.meta = type("M", (), {"description": type("D", (), {"text": text})()})()

    # docling 이 이미 본문에 설명을 넣어 준 경우
    md = "앞\n\n<!-- image -->\n\n매출이 늘었다"
    out, described = inject_picture_descriptions(
        md, type("Doc", (), {"pictures": [Picture("매출이 늘었다")]})()
    )
    assert described == 1
    assert out.count("매출이 늘었다") == 2  # 이미지 alt 텍스트 + 원래 설명 (중복 단락 없음)
    assert "**그림 설명(사내 모델):**" not in out


def test_chat_endpoint_normalizes_url():
    assert chat_endpoint("https://gw/v1") == "https://gw/v1/chat/completions"
    assert chat_endpoint("https://gw/v1/") == "https://gw/v1/chat/completions"
    assert (
        chat_endpoint("https://gw/v1/chat/completions") == "https://gw/v1/chat/completions"
    )


def test_marker_llm_config_points_at_gateway():
    config = marker_llm_config(
        base_url="https://gw.example/v1",
        model="corp-vl-32b",
        api_key="",
        service="marker.services.openai.OpenAIService",
    )
    assert config["use_llm"] is True
    assert config["llm_service"].endswith("OpenAIService")
    assert config["openai_base_url"] == "https://gw.example/v1"
    assert config["openai_model"] == "corp-vl-32b"
    assert config["openai_api_key"]  # openai SDK 는 빈 키를 거부한다


def test_mineru_vlm_backend_builds_client_command(tmp_path):
    from doc2md.backends.mineru_backend import MinerUBackend

    cfg = Config()
    cfg.engine_options["mineru"] = {
        "use_llm": True,
        "server_url": "http://mineru-vlm.corp:30000",
    }
    backend = MinerUBackend(cfg)
    cmd = backend.build_command("mineru", tmp_path / "a.pdf", tmp_path, "vlm-http-client")
    assert "-b" in cmd and cmd[cmd.index("-b") + 1] == "vlm-http-client"
    assert cmd[cmd.index("-u") + 1] == "http://mineru-vlm.corp:30000"


def test_mineru_vlm_backend_requires_server_url(tmp_path):
    from doc2md.backends.mineru_backend import MinerUBackend

    cfg = Config()
    cfg.engine_options["mineru"] = {"use_llm": True}
    backend = MinerUBackend(cfg)
    with pytest.raises(ConfigError, match="서버 주소"):
        backend.build_command("mineru", tmp_path / "a.pdf", tmp_path, "vlm-http-client")


def test_mineru_server_url_cannot_point_outside(tmp_path):
    from doc2md.backends.mineru_backend import MinerUBackend

    cfg = Config()
    cfg.engine_options["mineru"] = {
        "use_llm": True,
        "server_url": "https://api.openai.com/v1",
    }
    backend = MinerUBackend(cfg)
    with pytest.raises(ConfigError, match="외부 상용 LLM API"):
        backend.build_command("mineru", tmp_path / "a.pdf", tmp_path, "vlm-http-client")


def test_set_engine_options_applies_to_all_engines_when_auto():
    from doc2md.config import set_engine_options

    cfg = Config()
    set_engine_options(cfg, "auto", {"use_llm": True}, all_engines=list(ENGINES))
    assert cfg.engine_options["docling"]["use_llm"] is True
    assert cfg.engine_options["markitdown"]["use_llm"] is True

    cfg2 = Config()
    set_engine_options(cfg2, "docling", {"use_llm": True}, all_engines=list(ENGINES))
    assert list(cfg2.engine_options) == ["docling"]
