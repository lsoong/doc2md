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
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
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
        data = self._post("/v1/messages", payload)
        try:
            return "".join(
                block.get("text", "") for block in data["content"] if block.get("type") == "text"
            )
        except (KeyError, TypeError) as exc:
            raise LLMError(f"예상치 못한 응답 형식: {json.dumps(data)[:300]}") from exc

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "LLMClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


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
