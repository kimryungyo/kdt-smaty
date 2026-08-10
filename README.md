# SMART DESK FIN

SMART DESK를 단일 FastAPI 프로세스와 `asyncio` 기반으로 재구성하는 프로젝트다.
설정, singleton container, task 관리와 애플리케이션 수명주기 위에 로컬 EMQX와
연결하는 비동기 MQTT 기반, Arduino 높이·ESP32 relay DeskIO 어댑터와 목표·수동
책상 제어기를 구현했다. FIN ESP32-C3 relay firmware는 clean build까지 완료했지만
upload와 실물 검증은 아직 수행하지 않았다. 영상은 카메라별 FFmpeg publisher가
기존 호스트 MediaMTX에 발행하고 Python은 RTSP에서 최신 frame 하나를 읽는다.

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

로컬 EMQX가 `127.0.0.1:1883`에서 실행 중인지 확인한 뒤 FastAPI를 실행한다.
최초 MQTT 연결과 구독을 완료하지 못하면 애플리케이션도 시작하지 않는다.

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

## 카메라 실행 전제

카메라 기능은 기본적으로 비활성화되어 있어 장치나 FFmpeg 없이 개발·테스트할 수
있다. 실제 실행 전에는 호스트에서 MediaMTX가 실행 중이고 `user-cam`,
`posture-cam` RTSP path publish를 허용해야 한다. FastAPI는 MediaMTX를 설치·시작·종료하지
않는다.

두 카메라의 실제 capture index, input format, 해상도와 FPS를 먼저 확인한 뒤 `.env`에
안정적인 `/dev/v4l/by-id/...` 경로와 값을 설정한다. `.env.example`의 capture 값은
검증 전 후보이며, 두 카메라의 역할을 제품명만으로 확정하지 않는다.

```bash
command -v ffmpeg
ffmpeg -version
ls -l /dev/v4l/by-id/
ffmpeg -hide_banner -f v4l2 -list_formats all -i /dev/v4l/by-id/<camera-device>
ss -ltn | rg ':8554\b'
```

검증한 값으로 `SMART_DESK_VISION__ENABLED=true`를 설정하면 FastAPI가 카메라마다
FFmpeg 자식 process 하나와 RTSP reader thread 하나를 시작한다. 종료 시 reader를 먼저
닫고 자신이 시작한 FFmpeg만 종료한다. MediaMTX는 계속 실행된다.

## 테스트

```bash
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q src tests
cd frontend && npm run build
```

FIN relay firmware의 native 계약 test와 ESP32-C3 build:

```bash
pio test -d firmware/relay-controller -e native
pio run -d firmware/relay-controller -e esp32-c3-devkitm-1
```

build는 장치를 변경하지 않는다. firmware upload와 실제 UP/DOWN은 relay 분리 검증과
사용자 승인 뒤에만 수행한다.

기본 테스트는 MQTT broker 없이 실행된다. 로컬 EMQX와 실제 QoS 1 발행·구독 및
재연결·재구독까지 확인하려면 다음 명령을 추가로 실행한다.

```bash
SMART_DESK_RUN_MQTT_INTEGRATION=1 \
  .venv/bin/python -m pytest -m mqtt_integration
```

전체 폴더와 파일 책임은 [프로젝트 구조](docs/PROJECT_STRUCTURE.md), 설계와 구현
순서는 [설계 문서](docs/README.md), 프런트엔드 실행 방식은
[React 대시보드](docs/architecture/frontend.md)에서 확인한다. 실제 구현은
[번호순 작업 목록](docs/tasks/README.md)을 따른다. 새 기능이나 구조를 계획할
때는 [계획 및 설계 가이드](docs/guides/README.md)를 먼저 확인한다.
