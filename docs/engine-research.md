# 문서→Markdown 변환 오픈소스 조사 (2026-09)

doc2md 가 어떤 엔진을 왜 골랐는지 기록. SYA-31 산출물.

## 1. 후보군

| 도구 | 라이선스 | 방식 | PDF | Office | 비고 |
|---|---|---|---|---|---|
| **MinerU** (OpenDataLab) | AGPL-3.0 | 레이아웃 모델 + 수식/표 전용 모델, VLM 백엔드 옵션 | ◎ | △(2.x부터 일부) | OmniDocBench 공개 점수 최상위 |
| **Docling** (IBM Research) | MIT | 레이아웃 모델 + TableFormer | ◎ | ◎ | PDF·Office 를 한 파이프라인으로 |
| **Marker** (datalab) | GPL-3.0 + 상용 조항 | Surya OCR + 레이아웃 | ◎ | ○ | 배치 처리량·수식 강점, `--use_llm` 옵션 |
| **MarkItDown** (Microsoft) | MIT | 포맷별 파서(텍스트 추출) | △ | ◎ | 매우 빠름, 포맷 커버리지 최광 |
| **PyMuPDF4LLM** | AGPL-3.0 | PyMuPDF 텍스트/표 추출 | ○ | ✕ | 압도적으로 빠름(페이지당 수십 ms) |
| **Unstructured** | Apache-2.0 | 하이브리드 | ○ | ○ | 요소 단위 출력, Markdown 은 부차적 |
| **Pandoc** | GPL-2.0 | 문서 모델 변환 | ✕ | ○(docx) | PDF 입력 불가, pptx/xlsx 미지원 |
| **LlamaParse / Mathpix** | 상용 SaaS | 클라우드 | ◎ | ○ | **문서가 사외로 나감 → 사내 문서엔 부적합** |

> 모델 연결 방침(2026-09-16): 변환 엔진이든 LLM 이든 **사내에서 자체 서빙하는 것만** 쓴다.
> 토큰당 과금되는 외부 모델(GPT·Claude·Gemini 등) 연결 경로는 코드에서 제거·차단했다.

## 2. 정확도 근거

OmniDocBench(CVPR 2025, 1,651페이지 / 10개 문서 유형 / 다국어)가 사실상 표준 벤치마크다.
2026년 기준 공개 수치 요약:

- **MinerU** VLM·하이브리드 백엔드가 OmniDocBench v1.6 에서 **95.2~95.4** 로 최상위.
- **Docling** 은 계층 분할 + 이미지 설명을 켠 구성에서 약 **94.1%** 의 자동 정확도 보고.
- **Marker** 는 OmniDocBench 상위권은 아니지만 **처리량이 가장 높고** 수식·코드 복원이 좋다.
- **MarkItDown** 은 PDF 구조 충실도에서 하위권. 다만 docx/pptx/xlsx 처럼 **원본이 이미 구조를**
  **갖고 있는 포맷**에서는 결과 차이가 거의 없고 수십 배 빠르다.

주의: 서로 다른 버전·데이터셋의 숫자를 한 표에 엮은 비교 글이 많다. 절대 수치를 믿기보다
**우리 문서로 직접 비교하는 것**이 맞고, 그래서 doc2md 에 `compare` 명령을 넣었다.

## 3. 선택

| 엔진 | 채택 이유 | 기본 설치 |
|---|---|---|
| `docling` | PDF·docx·pptx·xlsx 를 한 엔진으로, MIT 라이선스, 정확도 2위권 → **기본 주력** | 권장 |
| `mineru` | 정확도 최상위. 수식·복잡 표가 많은 기술 문서용 | 선택 |
| `marker` | 대량 배치·GPU 환경에서 처리량 확보용 | 선택 |
| `pymupdf4llm` | 텍스트 레이어 살아 있는 PDF 를 대량으로 빠르게 | 권장 |
| `markitdown` | 최광 포맷 커버리지 + 폴백. Office 포맷은 이것으로 충분한 경우가 많다 | 권장 |
| `vlm` | 사내 비전 모델로 페이지 이미지를 직접 읽는다. 스캔본·도장·수기 주석 대응 | 사내망 전용 |

제외: Pandoc(PDF 입력 불가), Unstructured(Markdown 품질이 위 셋보다 낮음),
LlamaParse·Mathpix(사외 전송 — 사내 문서 반출 금지).

라이선스 주의: **MinerU(AGPL)**, **PyMuPDF4LLM(AGPL)**, **Marker(GPL + 상용 조항)** 는
사내 내부 도구로 쓰는 데는 문제가 없지만, 이 코드를 외부에 배포하는 제품에 넣으려면
라이선스 검토가 필요하다. doc2md 본체는 이들을 **선택적 의존성**으로만 두고 직접 링크하지
않는다(기본 설치는 MIT 인 Docling·MarkItDown).

## 4. 사내 모델을 어디에 쓰나

세 갈래로 붙였다.

1. **`--refine`** — 어떤 엔진의 결과든 사내 LLM 이 조각 단위로 다듬는다. 깨진 표 복원,
   잘린 줄 병합, 머리말/쪽번호 제거. 가장 비용 대비 효과가 크다.
2. **`--engine vlm`** — 페이지를 이미지로 렌더링해 사내 비전 모델이 직접 Markdown 을 쓴다.
   규칙 기반 파서가 무너지는 스캔본·복잡 레이아웃 전용. 페이지 수만큼 비용이 든다.
3. **`compare --judge`** — 엔진별 결과를 사내 모델이 채점해 순위를 매긴다. 문서 유형별로
   어떤 엔진을 표준으로 삼을지 정할 때 쓴다.

**연결 대상은 사내에서 자체 서빙하는 모델뿐이다**(2026-09-16 방침 확정). 지원 방언은 OpenAI
호환 하나로 좁혔다 — vLLM·SGLang·Ollama·TGI·LiteLLM 등 자체 서빙 스택이 전부 이 형식을
내주기 때문이다. SDK 를 쓰지 않고 httpx 로 직접 호출해 사내 프록시·사설 인증서 환경에서
문제를 줄였다.

토큰당 과금되는 외부 API(OpenAI·Anthropic·Gemini·Bedrock·OpenRouter 등)는 주소 검사
(`config.ensure_self_hosted`)로 호출 전에 막고, marker 처럼 상용 서비스 구현을 내장한
엔진은 OpenAI 호환 서비스만 허용한다. 초기 구현에 있던 Anthropic Messages 방언은 사실상
Claude API 로 이어지는 통로라 통째로 들어냈다.

## 5. 엔진에 내장된 LLM 연결 (`--engine-llm`, 2026-09 추가)

위 세 갈래는 doc2md 가 엔진 **밖에서** 모델을 부르는 방식이다. 조사해 보니 주요 엔진들은
파이프라인 **안쪽에** 모델을 끼우는 자리를 이미 갖고 있었고, 대부분 "OpenAI 호환 주소를
달라"는 형태라 사내 게이트웨이를 그대로 물릴 수 있다. 엔진별로 붙는 자리가 다르다.

| 엔진 | 엔진이 제공하는 연결점 | doc2md 모드 | 실제로 모델이 보는 것 |
|---|---|---|---|
| MarkItDown | `MarkItDown(llm_client=, llm_model=, llm_prompt=)` | `caption` | 이미지 파일·PPT 슬라이드 안의 그림 |
| Docling | VLM 파이프라인 + `ApiVlmOptions`/`ApiVlmEngineOptions` | `vlm` | 페이지 이미지 전체 |
| Docling | `do_picture_description` + `PictureDescriptionApiOptions` | `picture` | 본문에서 잘라낸 그림만 |
| Marker | `--use_llm` + `marker.services.*` | `refine` | 애매한 표·수식 블록 |
| MinerU | `-b vlm-http-client -u <url>` | `vlm-http-client` | (MinerU2 전용 VLM 서버) |
| PyMuPDF4LLM | 없음 | — | `--refine` 으로 대체 |

구현할 때 걸린 것들:

- **MarkItDown 은 OpenAI 클라이언트 "객체"를 요구**하지만 실제로 쓰는 건
  `client.chat.completions.create` 하나뿐이다. openai 패키지를 깔지 않고
  `OpenAICompatClient` 로 흉내 내 사내 게이트웨이에 물렸다.
- **엔진이 직접 HTTP 를 치는 훅(docling·marker)은 OpenAI 호환만 받는다.** 자체 서빙 스택이
  모두 이 형식이라 `base_url` 하나로 통일했다.
- **marker 의 LLM 서비스 목록에는 Gemini·Claude·Vertex 가 섞여 있다.** 기본값이 Gemini 라
  그대로 쓰면 외부 과금 API 로 나간다. doc2md 는 `OpenAIService` 만 허용하고 나머지는
  설정 단계에서 거부한다.
- **docling 의 그림 설명 기본 임계값(`picture_area_threshold = 0.05`)이 작은 차트를 통째로
  건너뛴다.** 기본값을 0.02 로 낮추고 `picture_min_area` 옵션으로 열어 뒀다.
- **MinerU 의 `vlm-http-client` 는 범용 챗 모델이 아니다.** MinerU2 전용 가중치를 올린
  vLLM/SGLang 서버를 가리키므로 사내 챗 게이트웨이 주소를 넣으면 안 된다. 별도 옵션
  (`server_url` / `--llm-url`)으로 분리하고, 주소가 없으면 에러로 막는다.

## 출처

- [OmniDocBench (opendatalab)](https://github.com/opendatalab/OmniDocBench)
- [MinerU](https://github.com/opendatalab/mineru)
- [Docling: An Efficient Open-Source Toolkit for AI-driven Document Conversion (arXiv 2501.17887)](https://arxiv.org/pdf/2501.17887)
- [Docling vs Marker vs MinerU 벤치마크 정리 (2026-07)](https://adityamangal98.medium.com/docling-vs-marker-vs-mineru-the-ultimate-open-source-pdf-parser-benchmark-2026-which-is-best-a36ecbb6c6b1)
- [Best Open-Source PDF-to-Markdown Tools in 2026](https://themenonlab.blog/blog/best-open-source-pdf-to-markdown-tools-2026)
- [PDF→Markdown 재현 가능 벤치마크 저장소](https://github.com/pdfmarkdownapp/pdf-to-markdown-benchmark)
- [PDF Parsing for RAG 2026: MinerU vs Docling vs Marker](https://builderai.tools/blog/pdf-parsing-for-rag-mineru-docling-marker-compared)
- [MarkItDown — LLM 이미지 설명 옵션](https://github.com/microsoft/markitdown#optional-dependencies)
- [Docling — 원격 VLM/그림 설명 API 연결 예제](https://docling-project.github.io/docling/examples/vlm_pipeline_api_model/)
- [Marker — `--use_llm` 과 LLM 서비스 목록](https://github.com/datalab-to/marker#use-llms-to-improve-accuracy)
- [MinerU 2 — VLM 백엔드와 http-client 모드](https://github.com/opendatalab/mineru#quick-usage)
