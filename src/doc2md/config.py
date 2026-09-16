"""설정 로딩.

우선순위: CLI 인자 > 환경변수 > 설정파일(TOML) > 기본값.

설정파일 탐색 순서:
  1. --config 로 지정한 경로
  2. $DOC2MD_CONFIG
  3. ./doc2md.toml
  4. ~/.config/doc2md/config.toml
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

try:  # pragma: no cover - 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10
    import tomli as tomllib  # type: ignore[no-redef]

CONFIG_FILENAME = "doc2md.toml"
USER_CONFIG_PATH = Path.home() / ".config" / "doc2md" / "config.toml"


@dataclass
class LLMConfig:
    """사내 LLM 게이트웨이 접속 설정."""

    # openai: OpenAI 호환(vLLM·LiteLLM·Ollama·사내 게이트웨이 대부분)
    # anthropic: Anthropic Messages API 호환 게이트웨이
    api: str = "openai"
    base_url: str = ""
    api_key: str = ""
    # 텍스트 정제(refine)에 쓸 기본 모델
    model: str = ""
    # 페이지 이미지를 읽을 비전 모델. 비우면 model 을 그대로 쓴다.
    vision_model: str = ""
    # 엔진 내장 LLM 훅(docling·marker·markitdown)은 OpenAI 호환 엔드포인트만 받는다.
    # api = "anthropic" 인 게이트웨이를 쓰면서 엔진 훅도 쓰려면 여기에 OpenAI 호환
    # 주소를 따로 적는다. 비우면 base_url 을 그대로 쓴다.
    openai_base_url: str = ""
    timeout: float = 180.0
    max_tokens: int = 8192
    temperature: float = 0.0
    # 게이트웨이가 요구하는 추가 헤더 (예: {"X-Dept": "AI"})
    extra_headers: dict[str, str] = field(default_factory=dict)

    @property
    def effective_vision_model(self) -> str:
        return self.vision_model or self.model

    @property
    def effective_openai_base_url(self) -> str:
        """엔진 내장 훅이 쓸 OpenAI 호환 주소."""
        return self.openai_base_url or (self.base_url if self.api == "openai" else "")

    def require_openai_endpoint(self, who: str) -> None:
        """엔진 내장 LLM 훅은 OpenAI 호환 엔드포인트가 필요하다."""
        if not self.effective_openai_base_url:
            raise ConfigError(
                f"{who} 의 내장 LLM 연결은 OpenAI 호환 엔드포인트가 필요합니다.\n"
                "  현재 api = \"anthropic\" 입니다. 설정파일 [llm] 에 "
                "openai_base_url = \"https://.../v1\" 을 추가하거나,\n"
                "  --refine(후처리 정제) 또는 -e vlm(사내 비전 모델 엔진)을 쓰세요."
            )

    def require(self) -> None:
        """LLM 호출 전 필수 설정 검증."""
        missing = [k for k, v in (("base_url", self.base_url), ("model", self.model)) if not v]
        if missing:
            raise ConfigError(
                "사내 모델 설정이 비어 있습니다: "
                + ", ".join(missing)
                + "\n  doc2md config init 으로 설정파일을 만들거나 "
                "DOC2MD_BASE_URL / DOC2MD_MODEL 환경변수를 지정하세요."
            )


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    # 포맷별 기본 엔진. engine=auto 일 때 참조한다.
    default_engine: str = "auto"
    # 엔진별 추가 옵션. {"docling": {"ocr": true}, ...}
    engine_options: dict[str, dict[str, Any]] = field(default_factory=dict)
    source_path: Path | None = None


class ConfigError(RuntimeError):
    """설정이 잘못되었거나 부족할 때."""


def find_config_file(explicit: str | os.PathLike[str] | None = None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"설정파일을 찾을 수 없습니다: {path}")
        return path
    env_path = os.environ.get("DOC2MD_CONFIG")
    if env_path:
        path = Path(env_path).expanduser()
        if not path.is_file():
            raise ConfigError(f"DOC2MD_CONFIG 경로가 잘못되었습니다: {path}")
        return path
    local = Path.cwd() / CONFIG_FILENAME
    if local.is_file():
        return local
    if USER_CONFIG_PATH.is_file():
        return USER_CONFIG_PATH
    return None


def load_config(explicit: str | os.PathLike[str] | None = None) -> Config:
    """설정파일 + 환경변수를 합쳐 Config 를 만든다."""
    cfg = Config()
    path = find_config_file(explicit)
    if path is not None:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
        cfg = _from_mapping(raw)
        cfg.source_path = path
    _apply_env(cfg)
    return cfg


def _from_mapping(raw: dict[str, Any]) -> Config:
    llm_raw = dict(raw.get("llm") or {})
    known = {f for f in LLMConfig.__dataclass_fields__}
    unknown = set(llm_raw) - known
    if unknown:
        raise ConfigError(f"[llm] 섹션에 알 수 없는 키: {', '.join(sorted(unknown))}")
    llm = LLMConfig(**llm_raw)
    return Config(
        llm=llm,
        default_engine=str(raw.get("default_engine") or "auto"),
        engine_options={k: dict(v) for k, v in (raw.get("engines") or {}).items()},
    )


def _apply_env(cfg: Config) -> None:
    env = os.environ
    mapping = {
        "DOC2MD_API": "api",
        "DOC2MD_BASE_URL": "base_url",
        "DOC2MD_API_KEY": "api_key",
        "DOC2MD_MODEL": "model",
        "DOC2MD_VISION_MODEL": "vision_model",
        "DOC2MD_OPENAI_BASE_URL": "openai_base_url",
    }
    for env_key, attr in mapping.items():
        value = env.get(env_key)
        if value:
            setattr(cfg.llm, attr, value)
    if env.get("DOC2MD_ENGINE"):
        cfg.default_engine = env["DOC2MD_ENGINE"]
    # api_key 는 설정파일에 "env:VAR" 로 간접 참조할 수 있다.
    if cfg.llm.api_key.startswith("env:"):
        var = cfg.llm.api_key[4:]
        cfg.llm.api_key = env.get(var, "")


def override_llm(cfg: Config, **kwargs: Any) -> Config:
    """CLI 인자로 LLM 설정을 덮어쓴다 (None/빈값은 무시)."""
    updates = {k: v for k, v in kwargs.items() if v}
    if not updates:
        return cfg
    cfg.llm = replace(cfg.llm, **updates)
    return cfg


def set_engine_options(cfg: Config, engine: str, options: dict[str, Any], *, all_engines: list[str] | None = None) -> None:
    """엔진 옵션을 CLI 인자로 덮어쓴다.

    engine 이 auto 면 어떤 엔진이 뽑힐지 모르므로 후보 전체에 같은 값을 걸어 둔다.
    """
    updates = {k: v for k, v in options.items() if v not in (None, "")}
    if not updates:
        return
    targets = all_engines or [] if engine in ("", "auto") else [engine]
    for name in targets:
        cfg.engine_options.setdefault(name, {}).update(updates)


SAMPLE_CONFIG = """\
# doc2md 설정파일
# 위치: ./doc2md.toml 또는 ~/.config/doc2md/config.toml

# engine=auto 일 때 쓰는 기본값. 특정 엔진으로 고정하려면 "docling" 등으로 바꾼다.
default_engine = "auto"

[llm]
# 사내 게이트웨이가 OpenAI 호환이면 "openai", Anthropic 호환이면 "anthropic"
api = "openai"
base_url = "https://llm-gw.example.corp/v1"
# 키를 파일에 직접 적지 말고 환경변수 참조를 쓴다: "env:변수명"
api_key = "env:CORP_LLM_API_KEY"
model = "qwen3-32b-instruct"
# 스캔 PDF 등 이미지 기반 문서를 읽을 비전 모델 (없으면 model 을 그대로 사용)
vision_model = "qwen3-vl-32b"
timeout = 180.0
max_tokens = 8192
temperature = 0.0
# api = "anthropic" 게이트웨이를 쓰면서 엔진 내장 LLM 훅(docling/marker/markitdown)도
# 쓰려면 OpenAI 호환 주소를 따로 적는다.
# openai_base_url = "https://llm-gw.example.corp/v1"
# 게이트웨이가 요구하는 추가 헤더가 있으면
# extra_headers = { X-Dept = "AI" }

# ---------------------------------------------------------------- 엔진별 옵션
# 공통 키:
#   use_llm    = true      엔진에 내장된 LLM 연결을 켠다 (CLI: --engine-llm)
#   llm_mode   = "..."     엔진이 여러 연결 방식을 가질 때 고른다 (CLI: --llm-mode)
#   llm_model  = "..."     그 엔진이 쓸 모델 ID (CLI: --llm-model)
#                          비우면 비전 훅은 vision_model, 텍스트 훅은 model 을 쓴다
# 지원 현황은 `doc2md engines --llm` 으로 확인한다.

[engines.docling]
# 스캔 문서용 OCR. 기본은 자동 판단(false)
ocr = false
# use_llm = true
# llm_mode = "vlm"       # vlm: 페이지 전체를 사내 비전 모델이 읽는다
#                        # picture: 표·본문은 docling 이 읽고 그림 설명만 사내 모델이 붙인다
# llm_model = "qwen3-vl-32b"

[engines.markitdown]
# use_llm = true         # 이미지·PPT 그림에 사내 비전 모델이 설명을 붙인다
# llm_model = "qwen3-vl-32b"

[engines.marker]
# use_llm = true         # 표 병합·수식 복원에 사내 모델을 쓴다 (marker 의 --use_llm)
# llm_model = "qwen3-vl-32b"
# llm_service = "marker.services.openai.OpenAIService"  # 기본값

[engines.mineru]
# use_llm = true
# MinerU 의 VLM 백엔드는 "사내 챗 게이트웨이"가 아니라 MinerU2 VLM 가중치를 올린
# vLLM/SGLang 서버를 가리킨다. 주소를 반드시 따로 지정한다.
# server_url = "http://mineru-vlm.example.corp:30000"
# llm_mode = "vlm-http-client"

[engines.vlm]
# 페이지 렌더링 해상도 (DPI). 높을수록 정확하지만 느리고 토큰을 더 쓴다.
dpi = 200
max_pages = 0  # 0 = 제한 없음
"""
