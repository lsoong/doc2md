"""MinerU (OpenDataLab) 엔진.

OmniDocBench 기준 공개 정확도가 가장 높다(수식·복잡 표 강함). 파이썬 API 가 버전마다
바뀌어 왔으므로 안정적인 CLI(`mineru -p ... -o ...`)를 호출하고 산출물을 읽어 온다.

내장 LLM 연결: MinerU 2 의 VLM 백엔드(`-b`). 다만 이건 "아무 챗 모델"에 붙는 게
아니라 MinerU2 전용 VLM 가중치를 올린 서버를 가리킨다 — 사내 챗 게이트웨이 주소를
넣으면 안 된다. 사내에 MinerU VLM 을 vLLM/SGLang 으로 서빙해 뒀다면 그 주소를
engines.mineru.server_url (또는 --llm-url) 에 넣는다. 사내 챗 게이트웨이의 범용
비전 모델을 쓰고 싶으면 docling --llm-mode vlm 이나 -e vlm 쪽이 맞다.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from ..config import ConfigError, ensure_self_hosted
from .base import Backend, BackendUnavailable, ConversionResult, LLMHook


class MinerUBackend(Backend):
    name = "mineru"
    title = "MinerU (OpenDataLab)"
    extensions = (".pdf", ".png", ".jpg", ".jpeg")
    install_hint = "pip install 'mineru[core]'"
    priority = 90
    requires = ()
    llm_hooks = (
        LLMHook(
            "vlm-http-client",
            "사내에 서빙한 MinerU2 VLM 서버에 붙는다 (server_url / --llm-url 필요)",
            role="vision",
            default=True,
            uses_gateway=False,
        ),
        LLMHook(
            "vlm-transformers",
            "MinerU2 VLM 가중치를 이 컴퓨터에서 직접 돌린다 (GPU 권장)",
            role="vision",
            uses_gateway=False,
        ),
    )

    @classmethod
    def is_available(cls) -> bool:
        return shutil.which("mineru") is not None

    def convert(self, path: Path) -> ConversionResult:
        exe = shutil.which("mineru")
        if exe is None:
            raise BackendUnavailable(self.install_hint)

        started = time.perf_counter()
        used_mode = self.resolve_hook().mode if self.llm_enabled else ""
        with tempfile.TemporaryDirectory(prefix="doc2md-mineru-") as tmp:
            outdir = Path(tmp)
            cmd = self.build_command(exe, path, outdir, used_mode)
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.option("timeout", 1800))
            if proc.returncode != 0:
                raise RuntimeError(
                    f"mineru 실행 실패 (exit {proc.returncode})\n{proc.stderr[-1500:]}"
                )
            md_files = sorted(outdir.rglob("*.md"))
            if not md_files:
                raise RuntimeError(f"mineru 가 Markdown 을 만들지 않았습니다.\n{proc.stdout[-800:]}")
            # 가장 큰 .md 가 본문이다 (다른 하나는 목차/부가 파일인 경우가 있다)
            main = max(md_files, key=lambda p: p.stat().st_size)
            markdown = main.read_text(encoding="utf-8", errors="replace")
            images: dict[str, bytes] = {}
            for img in main.parent.rglob("*"):
                if img.suffix.lower() in {".png", ".jpg", ".jpeg"} and img.is_file():
                    images[img.name] = img.read_bytes()

        return self._result(
            markdown.strip(),
            path,
            elapsed=time.perf_counter() - started,
            images=images,
            engine_llm=used_mode,
        )

    def build_command(self, exe: str, path: Path, outdir: Path, llm_mode: str) -> list[str]:
        """실행할 mineru CLI 명령을 만든다 (테스트에서 그대로 검증한다)."""
        cmd = [exe, "-p", str(path), "-o", str(outdir)]
        backend = llm_mode or self.option("backend")  # pipeline | vlm-transformers 등
        if backend:
            cmd += ["-b", str(backend)]
        if llm_mode == "vlm-http-client":
            url = str(self.option("server_url", "") or self.option("llm_url", "") or "")
            if not url:
                raise ConfigError(
                    "MinerU 의 vlm-http-client 백엔드에는 MinerU2 VLM 서버 주소가 필요합니다.\n"
                    "  --llm-url http://mineru-vlm.사내:30000 또는 "
                    "설정파일 [engines.mineru] server_url 을 지정하세요.\n"
                    "  (사내 챗 게이트웨이 주소가 아닙니다 — 범용 비전 모델을 쓰려면 "
                    "docling --llm-mode vlm 또는 -e vlm 을 쓰세요.)"
                )
            ensure_self_hosted(url, "MinerU VLM 서버 주소(server_url)")
            cmd += ["-u", url]
        lang = self.option("lang")
        if lang:
            cmd += ["-l", str(lang)]
        return cmd
