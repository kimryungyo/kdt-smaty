# React 대시보드

React 개발 소스와 FastAPI를 같은 저장소에서 운영하는 현재 구조를 설명한다.
개발 시에는 두 서버를 사용하고, 운영 시에는 FastAPI 하나로 제공한다.

## UI 변경 원칙

대시보드의 화면을 추가하거나 수정할 때는 현재 제품의 디자인 언어와 정보 구조를
우선한다. 이미 존재하는 화면의 색상 토큰, 타이포그래피, 카드 밀도, 여백, 반응형
규칙, 버튼·모달 상호작용을 재사용해 새 기능이 같은 제품의 일부로 느껴지게 한다.
기능 구현을 이유로 독립된 "임시" 화면이나 서로 다른 디자인 체계를 추가하지 않는다.

외부 시안 또는 정적 HTML/CSS를 이식할 때는 먼저 React 구현 여부와 사용 가능한
asset을 확인한다. React 구현이면 불필요한 재작성 대신 현재 API 계약을 연결하고,
정적 구현이면 동일한 DOM·CSS 결과를 목표로 React 컴포넌트로 옮긴다. 완료 전에는
기준 화면과 이식 화면을 같은 viewport에서 전체 비교하고, 텍스트·아이콘·경계·여백
같은 작은 요소는 확대 화면으로도 확인한다. 차이가 있으면 기능 추가보다 시각 차이를
먼저 해소한다.

## 현재 구조

```text
frontend/
├── package.json
├── package-lock.json
├── vite.config.ts
├── tsconfig.json
├── index.html
└── src/
    ├── main.tsx
    ├── App.tsx
    └── styles.css
```

`frontend/dist`는 `npm run build` 결과이며 Git에 저장하지 않는다. 배포 또는
장비 설치 과정에서 반드시 다시 생성한다.

## 개발 모드

```text
Browser :5173 → Vite development server
                     ├─ React source와 hot reload
                     └─ /api, /health proxy → FastAPI :9090
```

```bash
cd /srv/smart-desk-fin
.venv/bin/uvicorn smart_desk.main:app --host 0.0.0.0 --port 9090 --workers 1
```

```bash
cd /srv/smart-desk-fin/frontend
npm ci
npm run dev
```

React의 API 요청은 호스트를 하드코딩하지 않고 `/api/...`, `/health/...` 같은
상대 경로를 사용한다. Vite proxy 설정은 `frontend/vite.config.ts`가 기준이다.

## 운영 모드

```text
npm run build → frontend/dist
                        │
Browser :9090 → FastAPI ├─ /api, /health
                        └─ React 정적 파일과 SPA fallback
```

`src/smart_desk/frontend.py`가 React build를 `/`에 연결한다. FastAPI API route가
정적 frontend보다 우선하므로 `/health`와 향후 `/api`는 React fallback에
가로막히지 않는다.

```bash
cd /srv/smart-desk-fin/frontend
npm ci
npm run build

cd /srv/smart-desk-fin
SMART_DESK_ENVIRONMENT=production \
  .venv/bin/uvicorn smart_desk.main:app --host 0.0.0.0 --port 9090 --workers 1
```

production에서 `frontend/dist/index.html`이 없으면 애플리케이션 시작 전에 오류로
처리한다. 외부 웹 서버를 사용하려면 `SMART_DESK_DASHBOARD__SERVE_FRONTEND=false`로
FastAPI의 정적 제공만 끌 수 있다.

## 설정

| 환경변수 | 기본값 | 역할 |
| --- | --- | --- |
| `SMART_DESK_DASHBOARD__SERVE_FRONTEND` | `true` | FastAPI의 React 정적 제공 여부 |
| `SMART_DESK_DASHBOARD__FRONTEND_DIRECTORY` | `frontend/dist` | 프로젝트 루트 기준 build 경로 |

## 카메라 미리보기

React는 Python API에서 JPEG 프레임을 반복 요청하지 않는다. `CameraPublisher`가
MediaMTX에 올린 스트림을 [Vision 관측 작업](../tasks/04-vision-observation.md)에서
WebRTC 또는 HLS 주소로 재생한다.

```text
Browser ─ WebRTC/HLS ─ MediaMTX
FastAPI ─ JSON API ─── Browser
```

FastAPI는 카메라 연결 상태, 최신 프레임 시각과 Vision 결과 같은 JSON만
제공한다. 개발·배포 환경에서 MediaMTX 주소가 달라질 수 있으므로 React 코드에
호스트를 하드코딩하지 않고 frontend 환경 설정 한 곳에서 관리한다. 최신 원본 프레임
제공 기반은 구현돼 있으며 구체적인 WebRTC/HLS 선택과 URL은
[Vision 관측 작업](../tasks/04-vision-observation.md)에서 실측 후 확정한다.

## 검증

```bash
cd /srv/smart-desk-fin/frontend
npm ci
npm run build

cd /srv/smart-desk-fin
.venv/bin/python -m pytest
```

프런트엔드 경로, Vite proxy, build 디렉터리 또는 FastAPI 연결 방법이 바뀌면 이
문서와 `tests/integration/test_application.py`를 함께 갱신한다.
