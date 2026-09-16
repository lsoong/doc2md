# doc2md

PDF·Word·PowerPoint·Excel 문서를 Markdown 으로 변환하는 CLI.
변환 엔진을 **옵션으로 바꿔 가며** 쓸 수 있고, **사내 LLM 게이트웨이**에 붙여 결과를 다듬거나
비전 모델로 직접 읽을 수 있다.

엔진 선정 근거와 벤치마크 출처는 [docs/engine-research.md](docs/engine-research.md).

## 설치

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -e '.[basic]'        # markitdown + pymupdf4llm (가볍다)
uv pip install -e '.[all]'          # + docling (권장, torch 포함 ~2GB)
uv pip install 'mineru[core]'       # 정확도 최상위 엔진 (선택)
uv pip install marker-pdf           # 배치 처리량 최고 엔진 (선택)
```

엔진은 **설치된 것만 활성화**된다. 확인:

```bash
doc2md engines          # 전체
doc2md engines -f pdf   # pdf 를 지원하는 엔진만
```

## 엔진

| 엔진 | 강점 | 대상 포맷 | 비고 |
|---|---|---|---|
| `mineru` | 정확도 최상위(OmniDocBench), 수식·복잡 표 | pdf, 이미지 | AGPL, 무겁다 |
| `docling` | PDF·Office 를 한 파이프라인으로, MIT | pdf docx pptx xlsx html 이미지 | **기본 주력** |
| `marker` | 배치 처리량, 수식·코드 | pdf docx pptx xlsx epub | GPL+상용조항 |
| `vlm` | 스캔본·도장·수기 주석. 사내 비전 모델이 직접 읽는다 | pdf, 이미지 | 페이지당 과금 |
| `pymupdf4llm` | 압도적으로 빠름(텍스트 레이어 PDF) | pdf epub 등 | AGPL |
| `markitdown` | 포맷 커버리지 최광, 폴백 | 거의 모든 포맷 | PDF 구조 보존 약함 |

`--engine auto`(기본)는 **포맷을 지원하는 설치된 엔진 중 우선순위가 가장 높은 것**을 고른다.
과금되는 `vlm` 은 자동 선택에서 제외되므로 쓰려면 명시해야 한다.

## 사용법

### 변환

```bash
doc2md convert 보고서.pdf                       # auto 엔진, 현재 디렉터리에 보고서.md
doc2md convert 보고서.pdf -e docling -o out/     # 엔진 지정 + 출력 위치
doc2md convert 문서함/ -r -o out/                # 디렉터리 일괄 (재귀)
doc2md convert 보고서.pdf --stdout | less        # 표준출력으로
```

- 같은 이름의 다른 포맷을 한 번에 변환하면(`보고서.pdf`, `보고서.docx`) 출력이
  `보고서.pdf.md`, `보고서.docx.md` 로 갈려 서로 덮어쓰지 않는다.
- 엔진이 이미지를 추출하면 `<이름>_images/` 에 함께 저장된다(`--no-images` 로 끄기).
- 파일 하나가 실패해도 나머지는 계속 변환되고, 종료 코드만 1 이 된다.

### 사내 모델 연동

```bash
doc2md config init                     # ~/.config/doc2md/config.toml 템플릿 생성
doc2md config show                     # 지금 적용되는 설정 확인 (키는 가려짐)
doc2md models                          # 게이트웨이가 주는 모델 목록
doc2md convert 보고서.pdf --refine --pick-model      # 모델을 골라서 결과 정제
doc2md convert 스캔본.pdf -e vlm -m corp-vl-32b      # 비전 모델로 직접 변환
```

설정파일(`./doc2md.toml` 또는 `~/.config/doc2md/config.toml`):

```toml
default_engine = "auto"

[llm]
api = "openai"                        # 또는 "anthropic"
base_url = "https://llm-gw.example.corp/v1"
api_key = "env:CORP_LLM_API_KEY"      # 키는 파일에 직접 적지 말고 환경변수 참조
model = "corp-llm-32b"
vision_model = "corp-vl-32b"

[engines.docling]
ocr = false                           # 스캔 PDF 면 true

[engines.vlm]
dpi = 200
max_pages = 0                         # 0 = 제한 없음
```

환경변수로도 덮어쓸 수 있다(설정파일보다 우선):
`DOC2MD_BASE_URL`, `DOC2MD_API_KEY`, `DOC2MD_MODEL`, `DOC2MD_VISION_MODEL`, `DOC2MD_API`,
`DOC2MD_ENGINE`, `DOC2MD_CONFIG`.

`--refine` 은 결과를 조각내서 사내 모델에 보내 깨진 표 복원·잘린 줄 병합·쪽번호 제거를 시킨다.
프롬프트가 "내용을 만들어내지 말 것"을 강제하지만, **모델이 손댄 결과이므로 중요한 문서는**
**원본과 대조**하는 것이 맞다.

### 엔진 비교 — 어떤 엔진을 표준으로 삼을지 고를 때

```bash
doc2md compare 보고서.pdf -o cmp/         # 엔진별로 변환해 지표 표를 출력
doc2md compare 보고서.pdf --judge         # + 사내 모델이 순위를 매긴다
doc2md compare 보고서.pdf --json          # 지표를 JSON 으로 (CI·집계용)
```

출력 예:

```
                             엔진 비교: sample.pdf
┏━━━━━━━━━━━━┳━━━━━┳━━━━━━┳━━━━━━┳━━━━━━┳━━━━┳━━━━━━━┳━━━━━━━━━┳━━━━━━┳━━━━━━━━┓
┃ 엔진       ┃  초 ┃ 글자 ┃ 제목 ┃ 목록 ┃ 표 ┃ 표 행 ┃ 표 정합 ┃ 수식 ┃ 잡음줄 ┃
┡━━━━━━━━━━━━╇━━━━━╇━━━━━━╇━━━━━━╇━━━━━━╇━━━━╇━━━━━━━╇━━━━━━━━━╇━━━━━━╇━━━━━━━━┩
│ docling    │ 7.8 │  524 │    5 │    2 │  1 │     6 │    1.00 │    0 │      0 │
│ pymupdf4l… │ 3.5 │  366 │    5 │    3 │  1 │     6 │    1.00 │    0 │      0 │
│ markitdown │ 0.1 │  476 │    0 │    0 │  1 │     6 │    1.00 │    0 │      0 │
└────────────┴─────┴──────┴──────┴──────┴────┴───────┴─────────┴──────┴────────┘
```

지표는 정답지 없이 "무엇이 얼마나 살아남았는지"를 보여 줄 뿐이다. **글자 수가 많다고 정확한
것이 아니다** — 표 정합(표 행들의 열 개수 일관성)과 잡음줄(쪽번호 등)을 함께 보고, 최종
판단은 눈으로 하거나 `--judge` 로 모델에게 맡긴다.

## 개발

```bash
python tests/make_samples.py tests/samples     # 한국어 샘플 docx/pptx/xlsx/pdf 생성
python -m pytest tests -q                      # 테스트 (23개)
python tests/stub_gateway.py 8777              # 사내 게이트웨이 흉내 서버
DOC2MD_BASE_URL=http://127.0.0.1:8777 DOC2MD_MODEL=corp-llm-32b doc2md models
```

사내망 접속 없이 `--refine`, `vlm`, `--judge` 경로를 전부 굴려 볼 수 있게 스텁 게이트웨이를
넣어 뒀다. 테스트도 이 스텁으로 돈다.

### 엔진 추가하기

1. `src/doc2md/backends/` 에 `Backend` 상속 클래스를 만든다.
2. `name` / `title` / `extensions` / `install_hint` / `priority` / `requires` 를 채운다.
3. `convert(path) -> ConversionResult` 를 구현한다.
4. `backends/__init__.py` 의 `ENGINE_CLASSES` 에 등록한다.

`requires` 에 적은 모듈이 없으면 자동으로 "미설치"로 표시되고 `auto` 선택에서 빠진다.

## 알아 둘 것

- **첫 실행이 느리다.** docling·marker·mineru 는 첫 변환 때 모델 가중치를 내려받는다
  (docling 기준 수백 MB, 1~2분). 폐쇄망이면 `~/.cache/huggingface` 를 미리 옮겨 둔다.
- **스캔 PDF** 는 텍스트 레이어가 없어 대부분 엔진이 빈 결과를 낸다. 경고가 뜨면
  `[engines.docling] ocr = true` 로 켜거나 `-e vlm` 을 쓴다.
- **라이선스**: mineru·pymupdf4llm 은 AGPL, marker 는 GPL+상용 조항이다. 사내 내부 도구로는
  문제없지만 외부 배포 제품에 넣으려면 검토가 필요하다. 기본 설치는 MIT 계열만 쓴다.
- **사외 SaaS 파서(LlamaParse·Mathpix 등)는 의도적으로 넣지 않았다.** 사내 문서가 외부로
  나가기 때문이다.
