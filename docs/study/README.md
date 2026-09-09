# 공부 자료

교수님 면담 전에 읽을 학습 자료입니다.

| 파일 | 무엇 |
|---|---|
| `주차배정-공부로드맵.pdf` | **본 자료** (18쪽). 무엇을 어떤 순서로 공부할지, 우리 주차장의 수학적 표현, 교수님께 드릴 질문지 |
| `study-guide.html` | 위 PDF 의 원본. 내용을 고치려면 이 파일을 고칩니다 |
| `parking_topology.py` | **단독 실행본.** 이 파일 하나만 있으면 됩니다 |

## 파이썬 파일 실행

```bash
python parking_topology.py
```

외부 라이브러리도, 나머지 프로젝트도 필요 없습니다. 우리 주차장의 실제 도면
데이터(노드 71 · 간선 80 · 주차면 120)가 안에 들어 있고, 그래프 표현 →
다익스트라 → 비용 행렬 → 배정(탐욕법 vs 헝가리안) → 혼잡 측정을 차례로
보여줍니다. 교수님께 노트북으로 띄워 보여드리기 좋습니다.

## PDF 다시 만들기

`study-guide.html` 을 고친 뒤, 헤드리스 Chrome 으로 인쇄합니다.

```powershell
# Windows
& 'C:\Program Files\Google\Chrome\Application\chrome.exe' `
  --headless=new --disable-gpu --run-all-compositor-stages-before-draw `
  --virtual-time-budget=20000 --no-pdf-header-footer `
  '--print-to-pdf=docs/study/주차배정-공부로드맵.pdf' `
  'file:///<절대경로>/docs/study/study-guide.html'
```

```bash
# macOS
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --headless=new --run-all-compositor-stages-before-draw \
  --virtual-time-budget=20000 --no-pdf-header-footer \
  --print-to-pdf=docs/study/주차배정-공부로드맵.pdf \
  "file://$PWD/docs/study/study-guide.html"
```

> `--virtual-time-budget` 은 웹폰트와 **KaTeX** 가 내려와 조판을 마칠 시간을 벌어 줍니다.
> 이 값이 없으면 수식이 렌더되기 전에 인쇄되어 LaTeX 소스가 그대로 찍힙니다.

## 수식을 고칠 때

문서 전체가 세리프입니다. **수식은 KaTeX 로 조판**하므로 교과서와 같은
Computer Modern 계열이 나옵니다. 산세리프 수식은 눈이 읽는 방식이 달라
잘 안 읽히기 때문입니다.

- 구분자는 `$$ … $$`(별행) 와 `$ … $`(줄 안)입니다.
  `\[ … \]` 를 쓰지 마세요 — 백슬래시가 셸·에디터를 거치며 한 겹 벗겨지면
  구분자가 `[` 로 바뀌어 본문의 `E[X]`, `O(n)` 같은 대괄호까지 수식으로
  잡아먹습니다. 실제로 그렇게 깨진 적이 있습니다.
- `aligned` 안의 행 구분자는 백슬래시 **두 개**여야 합니다. 스크립트로 파일을
  고칠 때 이게 한 개로 줄어들기 쉬우니, 고친 뒤에는 PDF 를 열어
  수식이 실제로 렌더됐는지 눈으로 확인하세요.
- 확인 방법:

  ```python
  import pymupdf
  d = pymupdf.open("docs/study/주차배정-공부로드맵.pdf")
  bad = [i + 1 for i, p in enumerate(d) if "sum_{" in p.get_text()]
  print("수식 실패 페이지:", bad or "없음")
  ```

## 내용의 근거

문서에 인용된 수치는 전부 실제 실행 결과입니다.

- 무안내 대비 주차소요 −47 %, 우회거리 −96 %, 처리량 +51 % → `runs/compare-baseline/`
- 할당 전략 4종 비교 → `runs/allocator-matrix/`
- 리틀의 법칙 계산과 그 한계 → `docs/ALLOCATION_MODEL.md`
