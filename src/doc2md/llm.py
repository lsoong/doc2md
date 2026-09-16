"""사내 LLM 게이트웨이 클라이언트.

OpenAI 호환(vLLM·LiteLLM·Ollama·대부분의 사내 게이트웨이)과 Anthropic Messages
호환 두 방언을 지원한다. SDK 의존 없이 httpx 로 직접 호출해 사내망 프록시·사설
인증서 환경에서도 문제가 없게 했다.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass

import httpx

from .config import LLMConfig


class LLMError(RuntimeError):
    """게이트웨이 호출 실패."""


@dataclass
class ImagePart:
    """비전 요청에 실어 보낼 이미지 한 장."""

    data: bytes
    media_type: str = "image/png"

    def as_data_url(self) -> str:
        return f"data:{self.media_type};base64,{base64.b64encode(self.data).decode()}"


class LLMClient:
    def __init__(self, cfg: LLMConfig) -> None:
        cfg.require()
        self.cfg = cfg
        self._client = httpx.Client(timeout=cfg.timeout, follow_redirects=True)

    # ---------------------------------------------------------------- 내부
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self.cfg.extra_headers}
        if self.cfg.api == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
            if self.cfg.api_key:
                headers["x-api-key"] = self.cfg.api_key
        elif self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        return headers

    def _url(self, path: str) -> str:
        base = self.cfg.base_url.rstrip("/")
        return f"{base}/{path.lstrip('/')}"

    def _post(self, path: str, payload: dict) -> dict:
        try:
            resp = self._client.post(self._url(path), headers=self._headers(), json=payload)
        except httpx.HTTPError as exc:  # 네트워크·타임아웃
            raise LLMError(f"게이트웨이 연결 실패: {exc}") from exc
        if resp.status_code >= 400:
            raise LLMError(
                f"게이트웨이 오류 {resp.status_code}: {resp.text[:500]}\n"
                f"  URL={self._url(path)} model={payload.get('model')}"
            )
        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            raise LLMError(f"응답을 JSON 으로 해석할 수 없습니다: {resp.text[:300]}") from exc

    # ---------------------------------------------------------------- 공개 API
    def list_models(self) -> list[str]:
        """게이트웨이가 제공하는 모델 목록. 사용자가 고를 수 있게 하기 위한 것."""
        path = "/models" if self.cfg.api == "openai" else "/v1/models"
        try:
            resp = self._client.get(self._url(path), headers=self._headers())
        except httpx.HTTPError as exc:
            raise LLMError(f"모델 목록 조회 실패: {exc}") from exc
        if resp.status_code >= 400:
            raise LLMError(f"모델 목록 조회 실패 {resp.status_code}: {resp.text[:300]}")
        data = resp.json().get("data", [])
        return sorted(str(item.get("id", "")) for item in data if item.get("id"))

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        images: list[ImagePart] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """텍스트(+이미지) 한 턴 요청. 응답 본문 문자열을 돌려준다."""
        model = model or self.cfg.model
        max_tokens = max_tokens or self.cfg.max_tokens
        if self.cfg.api == "anthropic":
            return self._complete_anthropic(prompt, system, images, model, max_tokens)
        return self._complete_openai(prompt, system, images, model, max_tokens)

    def chat(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """OpenAI 형식 messages 를 그대로 받는 저수준 호출.

        엔진 내장 LLM 훅에 게이트웨이를 끼워 넣을 때(OpenAICompatClient) 쓴다.
        게이트웨이가 Anthropic 방언이면 여기서 형식을 바꿔 준다.
        """
        model = model or self.cfg.model
        max_tokens = max_tokens or self.cfg.max_tokens
        if self.cfg.api == "anthropic":
            system, converted = _openai_to_anthropic(messages)
            payload: dict = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": self.cfg.temperature,
                "messages": converted,
            }
            if system:
                payload["system"] = system
            return self._text_from_anthropic(self._post("/v1/messages", payload))
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self.cfg.temperature,
        }
        return self._text_from_openai(self._post("/chat/completions", payload))

    def _complete_openai(
        self,
        prompt: str,
        system: str | None,
        images: list[ImagePart] | None,
        model: str,
        max_tokens: int,
    ) -> str:
        content: list[dict] | str
        if images:
            content = [{"type": "text", "text": prompt}]
            content += [
                {"type": "image_url", "image_url": {"url": img.as_data_url()}} for img in images
            ]
        else:
            content = prompt
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self.cfg.temperature,
        }
        data = self._post("/chat/completions", payload)
        return self._text_from_openai(data)

    @staticmethod
    def _text_from_openai(data: dict) -> str:
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"예상치 못한 응답 형식: {json.dumps(data)[:300]}") from exc

    @staticmethod
    def _text_from_anthropic(data: dict) -> str:
        try:
            return "".join(
                block.get("text", "") for block in data["content"] if block.get("type") == "text"
            )
        except (KeyError, TypeError) as exc:
            raise LLMError(f"예상치 못한 응답 형식: {json.dumps(data)[:300]}") from exc

    def _complete_anthropic(
        self,
        prompt: str,
        system: str | None,
        images: list[ImagePart] | None,
        model: str,
        max_tokens: int,
    ) -> str:
        blocks: list[dict] = []
        for img in images or []:
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img.media_type,
                        "data": base64.b64encode(img.data).decode(),
                    },
                }
            )
        blocks.append({"type": "text", "text": prompt})
        payload: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": self.cfg.temperature,
            "messages": [{"role": "user", "content": blocks}],
        }
        if system:
            payload["system"] = system
        return self._text_from_anthropic(self._post("/v1/messages", payload))

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "LLMClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


# --------------------------------------------------- OpenAI SDK 흉내(엔진 훅용)

def auth_headers(cfg: LLMConfig) -> dict[str, str]:
    """엔진(docling 등)이 직접 HTTP 를 칠 때 그대로 넘겨 줄 인증 헤더.

    엔진 내장 훅은 OpenAI 호환 엔드포인트만 부르므로 Bearer 로 통일한다.
    """
    headers = dict(cfg.extra_headers)
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    return headers


def _openai_to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
    """OpenAI 형식 messages 를 Anthropic Messages 형식으로 옮긴다."""
    system_parts: list[str] = []
    converted: list[dict] = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content")
        if role == "system":
            system_parts.append(content if isinstance(content, str) else _flatten_text(content))
            continue
        if isinstance(content, str):
            blocks: list[dict] = [{"type": "text", "text": content}]
        else:
            blocks = []
            for part in content or []:
                if part.get("type") == "text":
                    blocks.append({"type": "text", "text": part.get("text", "")})
                elif part.get("type") == "image_url":
                    url = (part.get("image_url") or {}).get("url", "")
                    media_type, data = _split_data_url(url)
                    if data:
                        blocks.append(
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": data,
                                },
                            }
                        )
        converted.append({"role": "assistant" if role == "assistant" else "user", "content": blocks})
    return "\n\n".join(p for p in system_parts if p), converted


def _flatten_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return ""


def _split_data_url(url: str) -> tuple[str, str]:
    """data:image/png;base64,XXXX → ("image/png", "XXXX")."""
    if not url.startswith("data:") or ";base64," not in url:
        return "image/png", ""
    head, data = url.split(";base64,", 1)
    return head[len("data:") :] or "image/png", data


@dataclass
class _ShimMessage:
    content: str
    role: str = "assistant"


@dataclass
class _ShimChoice:
    message: _ShimMessage


@dataclass
class _ShimResponse:
    choices: list[_ShimChoice]
    model: str = ""


class _ShimCompletions:
    def __init__(self, client: "LLMClient") -> None:
        self._client = client

    def create(self, *, model: str, messages: list[dict], **kwargs: object) -> _ShimResponse:
        max_tokens = kwargs.get("max_tokens")
        text = self._client.chat(
            messages,
            model=model,
            max_tokens=int(max_tokens) if isinstance(max_tokens, int) else None,
        )
        return _ShimResponse(choices=[_ShimChoice(message=_ShimMessage(content=text))], model=model)


class _ShimChat:
    def __init__(self, client: "LLMClient") -> None:
        self.completions = _ShimCompletions(client)


class OpenAICompatClient:
    """`client.chat.completions.create(...)` 만 흉내 내는 최소 OpenAI 클라이언트.

    MarkItDown 처럼 "OpenAI 클라이언트 객체를 달라"고 하는 라이브러리에 사내
    게이트웨이를 끼워 넣기 위한 것이다. openai 패키지를 깔지 않아도 되고,
    Anthropic 방언 게이트웨이도 그대로 받는다.
    """

    def __init__(self, client: LLMClient) -> None:
        self._client = client
        self.chat = _ShimChat(client)

    def close(self) -> None:
        self._client.close()


# ------------------------------------------------------------------ 프롬프트

REFINE_SYSTEM = (
    "당신은 문서 변환 결과를 다듬는 편집기입니다. 입력으로 받은 Markdown 조각을 "
    "정확하게 복원하되 내용을 새로 만들어내지 않습니다."
)

REFINE_PROMPT = """\
아래는 변환기가 뽑아낸 Markdown 조각입니다. 다음 규칙대로 정리해 주세요.

규칙:
1. 내용을 추가하거나 요약하지 마세요. 있는 내용만 정리합니다.
2. 깨진 표를 올바른 Markdown 표로 복원하세요. 열 개수를 맞추고 헤더 구분선을 넣습니다.
3. 문장 중간에서 잘린 줄바꿈은 이어 붙이고, 문단 구분은 빈 줄로 유지하세요.
4. 제목처럼 보이는 줄은 문서 구조에 맞는 #, ##, ### 로 바꾸세요.
5. 머리말·꼬리말·쪽번호처럼 반복되는 잡음은 제거하세요.
6. 수식은 $...$ / $$...$$ 로, 코드·설정값 블록은 ``` 로 감싸세요.
7. 원문 언어를 유지하세요. 번역하지 마세요.
8. 설명 없이 정리된 Markdown 본문만 출력하세요.

--- 조각 시작 ---
{chunk}
--- 조각 끝 ---
"""

VISION_SYSTEM = (
    "당신은 문서 페이지 이미지를 Markdown 으로 옮기는 변환기입니다. "
    "보이는 것만 옮기고 추측해서 채우지 않습니다."
)

VISION_PROMPT = """\
이 문서 페이지 이미지를 Markdown 으로 변환하세요.

규칙:
1. 읽기 순서대로 옮깁니다. 다단 편집이면 왼쪽 단을 모두 옮긴 뒤 오른쪽 단으로 갑니다.
2. 제목 계층은 #, ##, ### 로 표현합니다.
3. 표는 Markdown 표로 옮깁니다. 병합 셀은 값을 반복해 채우고, 표가 너무 복잡하면 HTML <table> 을 씁니다.
4. 수식은 $...$ / $$...$$ 로 옮깁니다.
5. 그림·차트는 ![설명](image) 형태로 자리와 간단한 설명만 남깁니다.
6. 머리말·꼬리말·쪽번호는 제외합니다.
7. 원문 언어를 유지하고 번역하지 않습니다.
8. 설명 없이 Markdown 본문만 출력합니다.
"""

# 엔진 내장 훅은 system 메시지를 따로 못 넣는 경우가 많아 프롬프트 한 덩어리로 쓴다.
ENGINE_VISION_PROMPT = VISION_SYSTEM + "\n\n" + VISION_PROMPT

CAPTION_PROMPT = """\
이 그림을 문서 맥락에서 쓸 수 있게 한국어로 설명하세요.
- 무엇을 나타내는 그림인지 한 문장으로 먼저 씁니다.
- 그림 안의 글자·숫자·축 이름·범례는 빠짐없이 옮깁니다.
- 표·차트면 값을 읽어 정리합니다.
- 보이지 않는 것을 지어내지 않습니다.
- 설명문만 출력하고 머리말을 붙이지 않습니다."""

JUDGE_SYSTEM = "당신은 문서 변환 품질 심사관입니다. 근거를 들어 냉정하게 채점합니다."

JUDGE_PROMPT = """\
같은 원본 문서를 서로 다른 변환 엔진이 Markdown 으로 바꾼 결과들입니다.
아래 기준으로 각 결과를 0~100 점으로 채점하세요.

채점 기준 (가중치):
- 내용 누락 없음 (40)
- 표 구조 보존 (25)
- 제목·목록 등 구조 보존 (20)
- 잡음(머리말/꼬리말/중복) 없음 (10)
- Markdown 문법 정확성 (5)

출력은 아래 JSON 하나만, 다른 설명 없이:
{{"ranking": [{{"engine": "...", "score": 0, "reason": "한 줄 근거"}}], "best": "엔진이름"}}

--- 변환 결과들 ---
{candidates}
"""
