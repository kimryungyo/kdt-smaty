# SMART DESK FIN

SMART DESK를 단일 FastAPI 프로세스와 `asyncio` 기반으로 재구성하는 프로젝트다.
현재 단계는 설정, singleton container, task 관리와 애플리케이션 수명주기를
제공하는 기본 골조다. 실제 MQTT·카메라·책상 장치는 아직 연결하지 않는다.
영상은 카메라별 FFmpeg publisher가 MediaMTX에 발행하고 Python은 RTSP를 읽는
구조로 구현할 예정이다.

## 개발 환경

백엔드:

```bash
cd /srv/smart-desk-fin
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

React 대시보드:

```bash
cd /srv/smart-desk-fin/frontend
npm ci
```

설정 이름과 기본값은 [`.env.example`](.env.example)을 참고한다. `.env`는
선택 사항이며, 같은 이름의 환경변수가 우선한다.

## 개발 실행

터미널 하나에서 FastAPI를 실행한다.

하드웨어 singleton 중복 생성을 막기 위해 Uvicorn worker는 반드시 하나만
사용한다.

worker 하나 안에서도 FastAPI 요청, MQTT, 높이 갱신과 책상 제어는 여러 async
task로 동시에 실행할 수 있다. 이 프로젝트에서는 별도 process lock이나 실행
관리자를 추가하지 않고 아래 명령을 표준으로 사용한다. 실제 책상을 연결한
상태에서는 프로세스를 자동 재시작하는 `--reload`를 사용하지 않는다.

```bash
.venv/bin/uvicorn smart_desk.main:app --host 0.0.0.0 --port 9090 --workers 1
```

다른 터미널에서 Vite 개발 서버를 실행한다. `/api`와 `/health` 요청은
`http://127.0.0.1:9090`으로 proxy한다.

```bash
cd /srv/smart-desk-fin/frontend
npm run dev
```

개발 대시보드는 `http://127.0.0.1:5173`에서 연다.

## 운영 실행

React를 빌드한 뒤 FastAPI만 실행한다. 빌드 결과는 `frontend/dist`에 생성되며,
FastAPI가 `/`에서 SPA로 제공한다.

```bash
cd /srv/smart-desk-fin/frontend
npm ci
npm run build

cd /srv/smart-desk-fin
.venv/bin/uvicorn smart_desk.main:app --host 0.0.0.0 --port 9090 --workers 1
```

`SMART_DESK_ENVIRONMENT=production`일 때 React 빌드가 없으면 FastAPI 시작을
실패 처리한다. 개발 환경에서는 빌드가 없어도 API만 실행할 수 있다.

상태 확인:

```bash
curl http://127.0.0.1:9090/health/live
curl http://127.0.0.1:9090/health/ready
```

## 테스트

```bash
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q src tests
cd frontend && npm run build
```

전체 폴더와 파일 책임은 [프로젝트 구조](docs/PROJECT_STRUCTURE.md), 설계와 구현
순서는 [설계 문서](docs/README.md), 프런트엔드 실행 방식은
[React 대시보드](docs/architecture/frontend.md)에서 확인한다. 실제 구현은
[번호순 작업 목록](docs/tasks/README.md)을 따른다.
