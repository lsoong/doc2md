"""설정 로딩.

우선순위: CLI 인자 > 환경변수 > 설정파일(TOML) > 기본값.

설정파일 탐색 순서:
  1. --config 로 지정한 경로
  2. $DOC2MD_CONFIG
  3. ./doc2md.toml
  4. ~/.config/doc2md/config.toml

사내 모델이 여러 개일 때는 [llm.profiles.<이름>] 으로 미리 등록해 두고 --profile 로
골라 쓴다. 각 프로필은 [llm] 의 공통값을 상속하고, 다른 값만 덮어쓴다.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:  # pragma: no cover - 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10
    import tomli as tomllib  # type: ignore[no-redef]

CONFIG_FILENAME = "doc2md.toml"
USER_CONFIG_PATH = Path.home() / ".config" / "doc2md" / "config.toml"

# 사내에서 자체 서빙하는 모델에만 붙인다. 아래는 토큰당 과금되는 상용 LLM API 라
# 주소가 설정에 들어오면 호출 전에 막는다 (GPT·Claude 등 외부 모델 연결 금지).
BLOCKED_HOST_PATTERNS: tuple[str, ...] = (
    r"(^|\.)openai\.com$",
    r"(^|\.)openai\.azure\.com$",
    r"(^|\.)cognitiveservices\.azure\.com$",
    r"(^|\.)services\.ai\.azure\.com$",
    r"(^|\.)anthropic\.com$",
    r"(^|\.)googleapis\.com$",
    r"(^|\.)mistral\.ai$",
    r"(^|\.)cohere\.(ai|com)$",
    r"(^|\.)groq\.com$",
    r"(^|\.)deepseek\.com$",
    r"(^|\.)together\.(ai|xyz)$",
    r"(^|\.)fireworks\.ai$",
    r"(^|\.)openrouter\.ai$",
    r"(^|\.)perplexity\.ai$",
    r"(^|\.)x\.ai$",
    r"(^|\.)replicate\.com$",
    r"(^|\.)huggingface\.co$",
    r"(^|\.)dashscope\.aliyuncs\.com$",
    r"(^|\.)moonshot\.cn$",
    r"(^|\.)upstage\.ai$",
    r"(^|\.)ntruss\.com$",          # 네이버 클로바 스튜디오
    r"^bedrock[^.]*\.[^.]+\.amazonaws\.com$",
)

_BLOCKED_HOSTS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in BLOCKED_HOST_PATTERNS)


def is_external_llm_host(url: str) -> bool:
    """주소가 과금되는 상용 LLM API 를 가리키는가."""
    host = urlsplit(url if "//" in url else f"//{url}").hostname or ""
    return any(pattern.search(host) for pattern in _BLOCKED_HOSTS)


def ensure_self_hosted(url: str, what: str) -> None:
    """사내 자체 서빙 엔드포인트가 아니면 막는다."""
    if not url or not is_external_llm_host(url):
        return
    host = urlsplit(url if "//" in url else f"//{url}").hostname or url
    raise ConfigError(
        f"{what} 가 외부 상용 LLM API 를 가리킵니다: {host}\n"
        "  doc2md 는 사내에서 자체 서빙하는 모델만 연결합니다 "
        "(GPT·Claude 등 과금되는 외부 모델 연결 금지).\n"
        "  vLLM·SGLang·Ollama·TGI·LiteLLM 등으로 띄운 사내 OpenAI 호환 주소를 지정하세요."
    )


@dataclass
class LLMConfig:
    """사내 LLM 게이트웨이 접속 설정 하나 (= 프로필 하나).

    사내에 자체 서빙한 OpenAI 호환 엔드포인트(vLLM·SGLang·Ollama·TGI·LiteLLM 등)만
    지원한다. 외부 상용 API 주소는 ensure_self_hosted() 에서 막는다.
    """

    # 프로필 이름·설명 (표시용, 설정파일의 섹션 이름에서 채워진다)
    name: str = ""
    description: str = ""
    base_url: str = ""
    api_key: str = ""
    # 텍스트 정제(refine)에 쓸 기본 모델
    model: str = ""
    # 페이지 이미지를 읽을 비전 모델. 비우면 model 을 그대로 쓴다.
    vision_model: str = ""
    timeout: float = 180.0
    max_tokens: int = 8192
    temperature: float = 0.0
    # 게이트웨이가 요구하는 추가 헤더 (예: {"X-Dept": "AI"})
    extra_headers: dict[str, str] = field(default_factory=dict)

    @property
    def effective_vision_model(self) -> str:
        return self.vision_model or self.model

    @property
    def label(self) -> str:
        """오류 메시지에 쓸 이름."""
        return f"프로필 '{self.name}'" if self.name else "[llm]"

    def require(self) -> None:
        """LLM 호출 전 필수 설정 검증."""
        missing = [k for k, v in (("base_url", self.base_url), ("model", self.model)) if not v]
        if missing:
            raise ConfigError(
                f"사내 모델 설정({self.label})이 비어 있습니다: "
                + ", ".join(missing)
                + "\n  doc2md config init 으로 설정파일을 만들거나 "
                "DOC2MD_BASE_URL / DOC2MD_MODEL 환경변수를 지정하세요.\n"
                "  등록된 사내 모델은 doc2md profiles 로 확인하고 --profile 로 고릅니다."
            )
        ensure_self_hosted(self.base_url, f"{self.label} 의 base_url")


@dataclass
class Config:
    # 현재 선택된 프로필 (해석이 끝난 값)
    llm: LLMConfig = field(default_factory=LLMConfig)
    # 설정파일에 등록해 둔 사내 모델들. {이름: 설정}
    profiles: dict[str, LLMConfig] = field(default_factory=dict)
    # 지금 고른 프로필 이름 (프로필 없이 [llm] 만 쓰면 빈 문자열)
    active_profile: str = ""
    # --profile 을 생략했을 때 쓸 프로필
    default_profile: str = ""
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


def load_config(
    explicit: str | os.PathLike[str] | None = None,
    *,
    profile: str | None = None,
) -> Config:
    """설정파일 + 환경변수를 합쳐 Config 를 만든다.

    profile 은 [llm.profiles.<이름>] 중 하나. 생략하면 $DOC2MD_PROFILE >
    default_profile > (프로필이 하나뿐이면) 그것 > [llm] 순으로 고른다.
    """
    cfg = Config()
    path = find_config_file(explicit)
    if path is not None:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
        cfg = _from_mapping(raw)
        cfg.source_path = path
    select_profile(cfg, profile)
    _apply_env(cfg)
    return cfg


def select_profile(cfg: Config, name: str | None = None) -> Config:
    """쓸 프로필을 정해 cfg.llm 에 앉힌다.

    cfg.llm 에는 복사본을 넣는다 — 환경변수·CLI 덮어쓰기가 등록된 프로필 원본을
    건드리지 않게 하기 위해서다.
    """
    wanted = (name or os.environ.get("DOC2MD_PROFILE") or "").strip()
    if not wanted:
        if cfg.default_profile:
            wanted = cfg.default_profile
        elif len(cfg.profiles) == 1:
            wanted = next(iter(cfg.profiles))
    if not wanted:
        cfg.active_profile = ""
        return cfg
    if wanted not in cfg.profiles:
        known = ", ".join(cfg.profiles) or "(등록된 프로필 없음)"
        raise ConfigError(
            f"모르는 프로필입니다: {wanted}\n"
            f"  등록된 프로필: {known}\n"
            "  설정파일에 [llm.profiles.<이름>] 으로 추가하거나 doc2md profiles 로 확인하세요."
        )
    cfg.active_profile = wanted
    cfg.llm = _copy_llm(cfg.profiles[wanted])
    return cfg


def _copy_llm(llm: LLMConfig) -> LLMConfig:
    return replace(llm, extra_headers=dict(llm.extra_headers))


# 지원을 끊은 키 → 안내 문구. 예전 설정파일을 그대로 쓰면 이유를 알려 준다.
REMOVED_LLM_KEYS = {
    "api": (
        "api 키는 없어졌습니다. doc2md 는 OpenAI 호환 사내 엔드포인트만 지원합니다 "
        "(Anthropic 방언은 외부 상용 Claude API 로 이어질 수 있어 제거했습니다)."
    ),
    "openai_base_url": "openai_base_url 은 없어졌습니다. base_url 하나만 쓰세요.",
}


def _llm_section(raw: dict[str, Any], where: str, *, base: LLMConfig | None = None) -> LLMConfig:
    """[llm] / [llm.profiles.<이름>] 한 섹션을 LLMConfig 로 바꾼다.

    base 가 있으면 그 값을 상속하고 섹션에 적힌 키만 덮어쓴다 — 게이트웨이 주소·키는
    [llm] 에 한 번만 쓰고 프로필에는 모델 이름만 적을 수 있게 하기 위해서다.
    """
    values = dict(raw)
    for key, hint in REMOVED_LLM_KEYS.items():
        if key in values:
            raise ConfigError(f"{where} {hint}")
    unknown = set(values) - set(LLMConfig.__dataclass_fields__)
    if unknown:
        raise ConfigError(f"{where} 섹션에 알 수 없는 키: {', '.join(sorted(unknown))}")
    if base is None:
        return LLMConfig(**values)
    merged = replace(_copy_llm(base), **values)
    return merged


def _from_mapping(raw: dict[str, Any]) -> Config:
    if "profiles" in raw:
        raise ConfigError(
            "최상위 [profiles] 는 쓰지 않습니다. [llm.profiles.<이름>] 으로 적으세요 "
            "([llm] 의 공통값을 상속합니다)."
        )
    llm_raw = dict(raw.get("llm") or {})
    profiles_raw = llm_raw.pop("profiles", None) or {}
    if not isinstance(profiles_raw, dict):
        raise ConfigError("[llm.profiles] 는 [llm.profiles.<이름>] 표들로 적어야 합니다.")
    base = _llm_section(llm_raw, "[llm]")

    profiles: dict[str, LLMConfig] = {}
    for name, section in profiles_raw.items():
        if not isinstance(section, dict):
            raise ConfigError(f"[llm.profiles.{name}] 는 표(table)여야 합니다.")
        llm = _llm_section(section, f"[llm.profiles.{name}]", base=base)
        llm.name = name
        profiles[name] = llm

    default_profile = str(raw.get("default_profile") or "")
    if default_profile and default_profile not in profiles:
        known = ", ".join(profiles) or "(등록된 프로필 없음)"
        raise ConfigError(
            f"default_profile 이 등록되지 않은 프로필을 가리킵니다: {default_profile}\n"
            f"  등록된 프로필: {known}"
        )
    return Config(
        llm=base,
        profiles=profiles,
        default_profile=default_profile,
        default_engine=str(raw.get("default_engine") or "auto"),
        engine_options={k: dict(v) for k, v in (raw.get("engines") or {}).items()},
    )


def _apply_env(cfg: Config) -> None:
    env = os.environ
    mapping = {
        "DOC2MD_BASE_URL": "base_url",
        "DOC2MD_API_KEY": "api_key",
        "DOC2MD_MODEL": "model",
        "DOC2MD_VISION_MODEL": "vision_model",
    }
    for env_key, attr in mapping.items():
        value = env.get(env_key)
        if value:
            setattr(cfg.llm, attr, value)
    if env.get("DOC2MD_ENGINE"):
        cfg.default_engine = env["DOC2MD_ENGINE"]
    # api_key 는 설정파일에 "env:VAR" 로 간접 참조할 수 있다.
    cfg.llm.api_key = resolve_api_key(cfg.llm.api_key)


def resolve_api_key(value: str) -> str:
    """설정파일의 "env:VAR" 간접 참조를 실제 값으로 바꾼다."""
    if value.startswith("env:"):
        return os.environ.get(value[4:], "")
    return value


def ready_llm(llm: LLMConfig) -> LLMConfig:
    """실제 호출에 바로 쓸 수 있게 env: 참조를 푼 복사본."""
    return replace(_copy_llm(llm), api_key=resolve_api_key(llm.api_key))


def api_key_status(llm: LLMConfig) -> str:
    """프로필 목록에 보여 줄 키 상태 (값 자체는 절대 노출하지 않는다)."""
    raw = llm.api_key
    if not raw:
        return "(미설정)"
    if raw.startswith("env:"):
        var = raw[4:]
        return f"{var} 설정됨" if os.environ.get(var) else f"{var} 미설정"
    return "설정됨"


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

# --profile 을 생략했을 때 쓸 사내 모델. 아래 [llm.profiles.*] 중 하나를 적는다.
default_profile = "fast"

# ------------------------------------------------------------------ 사내 모델
# [llm] 은 모든 프로필이 공유하는 공통값이다. 프로필에는 달라지는 것만 적는다.
[llm]
# 사내에 자체 서빙한 OpenAI 호환 엔드포인트만 넣는다
# (vLLM·SGLang·Ollama·TGI·LiteLLM 등). OpenAI·Anthropic 같은 과금 API 주소는 거부된다.
base_url = "https://llm-gw.example.corp/v1"
# 키를 파일에 직접 적지 말고 환경변수 참조를 쓴다: "env:변수명"
api_key = "env:CORP_LLM_API_KEY"
model = "qwen3-32b-instruct"
# 스캔 PDF 등 이미지 기반 문서를 읽을 비전 모델 (없으면 model 을 그대로 사용)
vision_model = "qwen3-vl-32b"
timeout = 180.0
max_tokens = 8192
temperature = 0.0
# 게이트웨이가 요구하는 추가 헤더가 있으면
# extra_headers = { X-Dept = "AI" }

# 사내 모델이 여럿이면 여기에 등록해 두고 `doc2md convert ... --profile 이름` 으로 고른다.
# 목록·연결 확인: doc2md profiles [--check]
[llm.profiles.fast]
description = "일반 문서용 경량 모델 (기본값)"
model = "qwen3-8b-instruct"
vision_model = "qwen3-vl-8b"

[llm.profiles.accurate]
description = "표·수식 많은 문서용 대형 모델"
model = "qwen3-72b-instruct"
vision_model = "qwen3-vl-32b"
timeout = 600.0

[llm.profiles.local]
description = "개발자 노트북의 Ollama"
# base_url 처럼 [llm] 과 달라지는 값만 덮어쓴다
base_url = "http://127.0.0.1:11434/v1"
api_key = ""
model = "gemma3:27b"
vision_model = "gemma3:27b"

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
# llm_service 는 OpenAI 호환 서비스만 허용한다(기본값).
# Gemini·Claude·Vertex 등 marker 의 상용 서비스는 거부된다.
# llm_service = "marker.services.openai.OpenAIService"

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
