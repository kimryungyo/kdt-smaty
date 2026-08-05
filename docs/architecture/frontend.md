# React 대시보드

React 개발 소스와 FastAPI를 같은 저장소에서 운영하는 현재 구조를 설명한다.
개발 시에는 두 서버를 사용하고, 운영 시에는 FastAPI 하나로 제공한다.

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
