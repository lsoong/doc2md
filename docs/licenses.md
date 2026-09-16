# doc2md 라이선스 점검 (2026-09-16 확인)

"엔진들을 그냥 가져다 써도 되나?"에 대한 답. 표만 보고 판단해도 되게 썼다.
확인 방법은 각 프로젝트의 LICENSE 원문·PyPI 메타데이터·HuggingFace 모델 카드이고,
근거 URL 을 항목마다 달았다. CLI 로도 볼 수 있다: `doc2md licenses`.

> 법률 자문이 아니다. 아래 "조건부" 로 표시된 항목을 실제로 하게 되면
> (사외 배포·사외 서비스) 그때 법무 검토를 받아야 한다.

## 1. 결론

**사내에서 사내 문서를 변환하는 용도라면 여섯 엔진 전부 그대로 쓸 수 있다.**
돈을 내야 하는 것도, 소스를 공개해야 하는 것도 없다. 내부 사용은 GPL/AGPL 이 말하는
"배포(distribution)"가 아니기 때문이다.

걸리는 건 아래 세 가지뿐이고, 셋 다 **지금 우리 쓰임새에서는 발동하지 않는다.**

| # | 조건 | 언제 문제가 되나 | 지금 상태 |
|---|---|---|---|
| 1 | PyMuPDF 계열(AGPL-3.0) | doc2md 를 **사외**에 배포하거나 사외 이용자에게 웹서비스로 제공할 때 | 사내 CLI 전용 → 해당 없음 |
| 2 | Marker 1.x(GPL-3.0) | 구버전을 설치한 채 사외 배포할 때 | `marker-pdf>=2.0` 으로 고정함(2.0 부터 Apache-2.0) |
| 3 | Marker 가중치(Open RAIL-M) | 매출·투자 5백만 달러를 넘는 조직이 상용으로 쓸 때 | SYAI 규모 미달 → 무상 사용 가능 |

## 2. 쓰임새별 판정

| 쓰임새 | 판정 | 비고 |
|---|---|---|
| 사내 PC·서버에서 사내 문서를 변환 | **문제 없음** | 여섯 엔진 모두 |
| 사내 직원용 내부 웹서비스로 제공 | **가능** | AGPL 엔진을 포함하면 사내 이용자에게 소스 접근을 열어 두면 된다(AGPL §13). 사내 git 저장소 링크로 충분 |
| 사외 고객에게 SaaS 로 제공 | **조건부** | AGPL 엔진(`pymupdf4llm`·`vlm`·MinerU 가중치) 제외하거나, 전체 소스 공개를 각오하거나, Artifex 상용 라이선스를 산다 |
| doc2md 코드 자체를 외부 공개·배포 | **가능(현재 형태)** | 본체는 MIT 이고 무거운 엔진은 전부 **선택적 의존성**이다. AGPL 패키지를 함께 묶어 배포하지만 않으면 된다 |
| 변환 결과물(.md)의 권리 | **원문서 그대로** | 어떤 엔진 라이선스도 산출물에 조건을 걸지 않는다. 사내 문서의 기밀 등급이 그대로 따라간다 |

## 3. 엔진별 상세

| 엔진 | 코드 라이선스 | 모델 가중치 | 사내 사용 | 주의 |
|---|---|---|---|---|
| **Docling** (IBM) | MIT | CDLA-Permissive-2.0 / Apache-2.0 | 자유 | 없음. 기본 주력으로 삼기 가장 안전하다 |
| **MarkItDown** (Microsoft) | MIT | (모델 없음) | 자유 | `[all]` 선택 의존성만 별도 확인 |
| **Marker** (datalab) | **2.0+ Apache-2.0**, 1.x 는 GPL-3.0-or-later | 수정 AI Pubs Open RAIL-M | 가능 | 버전 고정 필수, 5백만 달러 기준 |
| **MinerU** (OpenDataLab) | MinerU Open Source License (Apache-2.0 기반) | AGPL-3.0 표기 | 가능 | 코드는 완화됐지만 가중치 표기가 아직 AGPL |
| **PyMuPDF4LLM** | AGPL-3.0 또는 Artifex 상용 | (모델 없음) | 가능 | 사외 배포·서비스 시 전염 |
| **vlm** (doc2md 자체) | doc2md MIT + PyMuPDF AGPL | 사내 모델에 의존 | 가능 | PyMuPDF 와 동일 조건 |

### Docling — MIT

코드 MIT, 가중치는 `ds4sd/docling-models` 가 CDLA-Permissive-2.0 / Apache-2.0.
둘 다 상용·배포·수정에 조건이 없다(귀속 표시만). 기본 설치 조합(Docling + MarkItDown)이
전부 허용적 라이선스인 건 의도된 선택이다.
근거: <https://github.com/docling-project/docling/blob/main/LICENSE>,
<https://huggingface.co/ds4sd/docling-models>

### MarkItDown — MIT

코드 MIT. 모델을 내려받지 않는다(LLM 은 우리가 사내 게이트웨이로 붙인다).
`markitdown[all]` 로 딸려오는 파서 의존성은 설치본 기준 전부 MIT·BSD·Apache 계열이었다(§4).
근거: <https://github.com/microsoft/markitdown/blob/main/LICENSE>

### Marker — 2.0 부터 Apache-2.0 (이전엔 GPL-3.0)

**이번 점검에서 바뀐 사실.** 이전 조사 기록(`engine-research.md`)에는 "GPL-3.0 + 상용 조항"
으로 적혀 있었는데, PyPI 메타데이터를 다시 보니 `marker-pdf` 는 1.10.1 이 `GPL-3.0-or-later`,
2.0.0 이 `Apache-2.0` 이다. 즉 2.x 에서 코드가 허용적 라이선스로 풀렸다.

대신 **모델 가중치**는 "수정 AI Pubs Open RAIL-M" 으로 남아 있다 — 연구·개인·
**매출/투자 5백만 달러 미만 조직**은 무상, 그 이상은 datalab 상용 라이선스가 필요하다.
Surya OCR(marker 가 내부에서 쓰는 OCR)도 코드 Apache-2.0 / 가중치 동일 조건이다.

조치: `pyproject.toml` 의 extra 를 `marker-pdf>=2.0` 으로 올렸다. 1.x 가 깔리면 GPL 코드가
섞이므로 사외 배포 시 전염된다.
근거: <https://pypi.org/project/marker-pdf/>, <https://github.com/datalab-to/marker/blob/master/LICENSE>

### MinerU — AGPL-3.0 → MinerU Open Source License

**이것도 바뀐 사실.** MinerU 는 `AGPLv3` 에서 **Apache-2.0 기반 자체 라이선스**로 이전했다.
추가 조건은 세 가지다.

1. 합산 **MAU 1억 명** 또는 **월매출 2천만 달러**를 넘으면 별도 상용 계약이 필요하다.
2. MinerU 를 쓰는 **온라인 서비스**는 UI 나 공개 문서에 MinerU 사용 사실을 명시해야 한다.
3. 위를 어기면 라이선스가 통지 없이 자동 종료된다.

우리 규모에선 1·3 은 무관하고, 2 는 사내 서비스로 노출할 때 "MinerU 사용" 한 줄만 적으면 된다.

다만 **가중치**는 사정이 다르다. `opendatalab/MinerU2.5-2509-1.2B` 모델 카드는
2026-09-16 확인 시점에 여전히 `agpl-3.0` 으로 표기돼 있다. 코드만 보고 "완전히 풀렸다"고
판단하면 안 되고, 사외 배포 시나리오에서는 AGPL 로 취급해야 한다.
근거: <https://github.com/opendatalab/MinerU/blob/master/LICENSE.md>,
<https://huggingface.co/opendatalab/MinerU2.5-2509-1.2B>

### PyMuPDF4LLM / vlm 엔진 — AGPL-3.0

PyMuPDF(및 pymupdf4llm, pymupdf-layout)는 **AGPL-3.0 또는 Artifex 상용 라이선스** 듀얼이다.
AGPL 은 GPL 의 조건에 더해 **§13(원격 네트워크 상호작용)**을 얹는다 — 네트워크로 프로그램을
이용하는 사람에게도 소스를 제공해야 한다.

- 사내 CLI 로 각자 실행 → 배포가 아니므로 아무 의무 없음.
- 사내 웹서비스로 제공 → 사내 이용자에게 소스 접근을 열어 두면 준수(사내 git 링크면 충분).
- 사외 서비스·제품 → 전체를 AGPL 로 공개하거나 Artifex 상용 라이선스 구매.

`vlm` 엔진은 페이지 렌더링에 PyMuPDF 를 쓰므로 같은 조건이 걸린다. 이 둘을 빼면
doc2md 의 AGPL 노출은 0 이 된다(기본 설치 조합에는 원래 들어 있지 않다).
근거: <https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright>

## 4. 파이썬 의존성 전수 확인

현재 개발 환경(docling + markitdown + pymupdf + 테스트 도구) 기준 설치 패키지 **134개**의
라이선스 분포:

| 라이선스 | 개수 | 비고 |
|---|---|---|
| MIT | 73 | |
| BSD 계열 | 34 | |
| Apache-2.0 | 19 | |
| PSF | 2 | |
| **AGPL-3.0 (듀얼)** | **3** | `pymupdf`, `pymupdf4llm`, `pymupdf-layout` |
| MPL-2.0 | 2 | `certifi`, `tqdm` — 파일 단위 약한 카피레프트, 수정 없이 쓰면 의무 없음 |
| ISC | 1 | |

즉 **카피레프트 위험은 PyMuPDF 세 패키지에만 몰려 있다.** 재확인 명령:

```bash
.venv/bin/python - <<'PY'
import importlib.metadata as m
for d in m.distributions():
    md = d.metadata
    lic = md.get('License-Expression') or '; '.join(
        c for c in (md.get_all('Classifier') or []) if 'License' in c
    ) or (md.get('License') or '')[:60]
    if any(k in lic.upper() for k in ('GPL', 'AFFERO', 'PROPRIETARY')):
        print(md['Name'], md['Version'], lic)
PY
```

marker·mineru 는 설치돼 있지 않아 전수 스캔에서 빠졌다. 실제로 도입할 때 같은 명령으로
그 의존성 트리(torch·surya 등)를 다시 훑어야 한다 — **남은 검증 항목**.

## 5. 사내 LLM 쪽 라이선스

doc2md 는 모델을 포함하지 않는다. 사내 게이트웨이에 올린 모델의 라이선스는 별개이고,
그건 서빙하는 쪽에서 확인해야 한다. 참고로 흔한 선택지는:

- Qwen 2.5/3 계열 — Apache-2.0 (72B 일부 버전은 별도 라이선스)
- Llama 3.x — Meta Llama Community License (MAU 7억 초과 시 별도 계약)
- Gemma — Gemma Terms of Use (사용 제한 조항 있음)

`doc2md profiles` 에 등록하는 프로필마다 어떤 모델인지 적어 두면(`description`) 나중에
이 확인이 쉬워진다.

## 6. 할 일

- [x] `marker-pdf>=2.0` 으로 하한 고정 (1.x GPL 회피)
- [x] 엔진별 라이선스를 코드에 박아 `doc2md licenses` 로 조회 가능하게
- [ ] marker·mineru 실제 설치 후 의존성 트리 전수 스캔
- [ ] 사외 서비스화가 논의되면: PyMuPDF 제외 빌드(=`docling`+`markitdown` 조합) 확인
- [ ] MinerU 가중치 라이선스 표기가 코드와 맞춰지는지 주기적 재확인
