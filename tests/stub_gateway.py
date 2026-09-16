"""사내 게이트웨이 흉내를 내는 로컬 HTTP 서버 (테스트·데모용).

사내망 접속 없이 --refine, vlm 엔진, --judge 경로를 그대로 굴려 볼 수 있다.
사내 자체 서빙 모델과 같은 OpenAI 호환 API(/models, /chat/completions)만 받는다.

단독 실행:
    python tests/stub_gateway.py 8777
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

MODELS = ["corp-llm-32b", "corp-vl-32b", "corp-llm-8b"]


class Handler(BaseHTTPRequestHandler):
    # 받은 요청을 기록해 테스트에서 검증한다.
    requests: list[dict] = []

    def log_message(self, *args: object) -> None:  # 콘솔 소음 제거
        pass

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.endswith("/models"):
            self._send({"data": [{"id": name} for name in MODELS]})
            return
        self._send({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        Handler.requests.append(
            {"path": self.path, "payload": payload, "headers": dict(self.headers)}
        )
        if self.path.endswith("/chat/completions"):
            text = _reply_for_openai(payload)
            # docling 처럼 응답을 엄격하게 검증하는 라이브러리가 있어 실제 게이트웨이와
            # 같은 필드(id·created·index·finish_reason·usage)를 모두 채워 준다.
            self._send(
                {
                    "id": f"chatcmpl-stub-{len(Handler.requests)}",
                    "object": "chat.completion",
                    "created": 1700000000,
                    "model": payload.get("model"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": text},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": len(text),
                        "total_tokens": len(text) + 1,
                    },
                }
            )
            return
        self._send({"error": "not found"}, status=404)


def _reply_for_openai(payload: dict) -> str:
    content = payload["messages"][-1]["content"]
    if isinstance(content, list):  # 비전 요청
        has_image = any(part.get("type") == "image_url" for part in content)
        return "## 비전 변환 결과\n\n| a | b |\n| --- | --- |\n| 1 | 2 |" if has_image else "(이미지 없음)"
    if "채점" in content:  # --judge 경로
        engines = [
            line.split("엔진:")[1].strip()
            for line in content.splitlines()
            if line.startswith("### 엔진:")
        ]
        ranking = [
            {"engine": name, "score": 90 - index * 10, "reason": "스텁 게이트웨이의 고정 응답"}
            for index, name in enumerate(engines)
        ]
        return json.dumps(
            {"ranking": ranking, "best": engines[0] if engines else ""}, ensure_ascii=False
        )
    return "## 정제됨\n\n" + content.strip().splitlines()[-2][:40]


class StubGateway:
    """with StubGateway() as gw: ... gw.base_url 로 접속."""

    def __init__(self, port: int = 0) -> None:
        Handler.requests = []
        self.server = HTTPServer(("127.0.0.1", port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def requests(self) -> list[dict]:
        return Handler.requests

    def __enter__(self) -> "StubGateway":
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8777
    with StubGateway(port) as gw:
        print(f"stub gateway: {gw.base_url}  (모델: {', '.join(MODELS)})")
        print("Ctrl+C 로 종료")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
