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

> `--virtual-time-budget` 은 웹폰트(Noto Serif KR)가 내려올 시간을 벌어 줍니다.
> 이 값이 없으면 한글이 세리프가 아닌 기본 글꼴로 인쇄됩니다.

## 내용의 근거

문서에 인용된 수치는 전부 실제 실행 결과입니다.

- 무안내 대비 주차소요 −47 %, 우회거리 −96 %, 처리량 +51 % → `runs/compare-baseline/`
- 할당 전략 4종 비교 → `runs/allocator-matrix/`
- 리틀의 법칙 계산과 그 한계 → `docs/ALLOCATION_MODEL.md`
