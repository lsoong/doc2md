"""MinerU (OpenDataLab) 엔진.

OmniDocBench 기준 공개 정확도가 가장 높다(수식·복잡 표 강함). 파이썬 API 가 버전마다
바뀌어 왔으므로 안정적인 CLI(`mineru -p ... -o ...`)를 호출하고 산출물을 읽어 온다.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .base import Backend, BackendUnavailable, ConversionResult


class MinerUBackend(Backend):
    name = "mineru"
    title = "MinerU (OpenDataLab)"
    extensions = (".pdf", ".png", ".jpg", ".jpeg")
    install_hint = "pip install 'mineru[core]'"
    priority = 90
    requires = ()

    @classmethod
    def is_available(cls) -> bool:
        return shutil.which("mineru") is not None

    def convert(self, path: Path) -> ConversionResult:
        exe = shutil.which("mineru")
        if exe is None:
            raise BackendUnavailable(self.install_hint)

        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="doc2md-mineru-") as tmp:
            outdir = Path(tmp)
            cmd = [exe, "-p", str(path), "-o", str(outdir)]
            backend = self.option("backend")  # pipeline | vlm-transformers 등
            if backend:
                cmd += ["-b", str(backend)]
            lang = self.option("lang")
            if lang:
                cmd += ["-l", str(lang)]
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
        )
