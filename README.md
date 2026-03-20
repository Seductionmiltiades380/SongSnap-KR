# SongSnap-KR 🎵

**PC 시스템 오디오에서 실시간으로 노래를 인식하고, 제목을 한국어로 번역해서 보여주는 도구입니다.**

유튜브, 스포티파이, 게임, 애니메이션 스트리밍 등 컴퓨터에서 재생 중인 모든 음악을 자동으로 인식하여 제목과 아티스트를 실시간 웹 UI로 표시합니다. 일본어 등 외국어 제목은 한국어로 함께 표시됩니다.

> **라이브 데모 →** [wjddusrb03.github.io/SongSnap-KR](https://wjddusrb03.github.io/SongSnap-KR) *(정적 미리보기, 백엔드 불필요)*

---

## 동작 원리

SongSnap-KR은 가사나 AI 추측을 사용하지 않습니다. **오디오 핑거프린팅** 방식을 사용합니다:

1. **루프백 오디오 캡처** — Windows WASAPI 루프백을 통해 PC 출력 오디오를 직접 녹음합니다. 마이크가 필요 없습니다.
2. **오디오 핑거프린팅** — 5초마다 오디오 샘플을 스펙트로그램으로 변환하고, 가장 에너지가 높은 주파수 피크를 추출하여 고유한 "지문"을 만듭니다.
3. **곡 매칭** — 핑거프린트를 인식 엔진에 전송하여 7천만 곡 이상의 DB와 대조, 1초 이내에 결과를 반환합니다.
4. **한국어 번역** — 인식된 제목과 아티스트명을 Google 번역을 통해 한국어로 함께 표시합니다.
5. **실시간 웹 UI** — WebSocket으로 브라우저에 즉시 결과를 전송합니다.

```
PC 오디오 출력
     │
     ▼
WASAPI 루프백 (마이크 불필요)
     │
     ▼ 5초마다
오디오 핑거프린트 (주파수 피크)
     │
     ▼
음악 인식 엔진
     │
     ▼
Google 번역 (→ 한국어)
     │
     ▼
WebSocket → 브라우저 UI
```

---

## 주요 기능

- **마이크 불필요** — 시스템 오디오를 직접 캡처
- **자동 감지** — 버튼을 누를 필요 없이 계속 듣고 있음
- **중복 필터링** — 같은 곡이 연속으로 두 번 표시되지 않음
- **무음 감지** — 재생 중인 오디오가 없으면 처리 건너뜀
- **인식 기록** — 세션 중 인식된 최근 50곡 표시
- **앨범 아트** — 고화질 커버 이미지 자동 표시
- **한국어 번역** — 원제목 옆에 한국어 번역 함께 표시
- **원클릭 실행** — `start.bat` 더블클릭 시 브라우저 자동 오픈
- **브라우저 내 종료** — 터미널을 건드리지 않아도 됨

---

## 요구 사항

- **운영체제:** Windows 10 / 11 (WASAPI 루프백은 Windows 전용)
- **Python:** 3.10 이상
- **네트워크:** 인터넷 연결 (곡 인식 및 번역)

---

## 설치 방법

**1. 저장소 클론**

```bash
git clone https://github.com/wjddusrb03/SongSnap-KR.git
cd SongSnap-KR
```

**2. 패키지 설치**

```bash
pip install -r requirements.txt
```

> `pyaudiowpatch` 빌드 오류가 발생하면 [visualstudio.microsoft.com](https://visualstudio.microsoft.com/visual-cpp-build-tools/)에서 Microsoft C++ Build Tools를 설치하세요.

---

## 사용 방법

**`start.bat` 더블클릭**

서버가 시작되고 브라우저가 자동으로 `http://localhost:8000`을 엽니다.

종료하려면 웹 페이지의 **"서버 종료"** 버튼을 클릭하세요.

---

## 인식 가능 여부

| 콘텐츠 | 결과 |
|---|---|
| 주류 음악 (모든 언어) | 잘 됨 |
| 애니메이션 OP/ED | 잘 됨 |
| 보컬로이드 (인기곡) | 대체로 됨 |
| 연주곡 | 됨 (핑거프린팅은 가사 불필요) |
| 커버곡 / 팬 어레인지 | 안 될 수 있음 (핑거프린트 다름) |
| 동인음악 / 자체 배포곡 | DB에 없을 수 있음 |
| 니코니코 전용 업로드 | 인식 어려움 |

인식되지 않는 곡은 조용히 건너뛰고 다음 5초 구간을 다시 시도합니다.

---

## 프로젝트 구조

```
SongSnap-KR/
├── app.py            # FastAPI 서버 + 오디오 캡처 + 곡 인식 + 번역
├── start.bat         # Windows 실행기 (서버 시작, 브라우저 자동 오픈)
├── requirements.txt  # Python 패키지 목록
└── docs/
    └── index.html    # GitHub Pages 정적 데모
```

---

## 기술 스택

| 구성 요소 | 라이브러리 |
|---|---|
| 웹 서버 | [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) |
| 실시간 전송 | WebSocket (FastAPI 내장) |
| 오디오 캡처 | [PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch) (WASAPI 루프백) |
| 곡 인식 | [ShazamAPI](https://github.com/dotX12/ShazamAPI) (비공식 서드파티 라이브러리) |
| 오디오 처리 | [NumPy](https://numpy.org/) |
| 번역 | [deep-translator](https://github.com/nidhaloff/deep-translator) (Google 번역) |

---

## 영어 버전

영어 UI / 번역 없는 버전은 [SongSnap](https://github.com/wjddusrb03/SongSnap)을 확인하세요.

---

## 면책 조항

이 프로젝트는 Shazam Entertainment Ltd. 또는 Apple Inc.와 **무관한 독립 오픈소스 프로젝트**입니다.

[ShazamAPI](https://github.com/dotX12/ShazamAPI)는 공식 제휴 없이 개발된 **비공식 서드파티 라이브러리**입니다. 모든 곡 메타데이터 및 인식 결과는 서드파티 서비스에 의해 제공됩니다.

**개인 비상업적 용도로만 사용하세요.**

---

## 라이선스

MIT License — 자유롭게 사용, 수정, 배포 가능합니다.
