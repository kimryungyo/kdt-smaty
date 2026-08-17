# 프로젝트 구조

현재 `smart-desk-fin`의 폴더·파일 역할과 새 코드를 배치하는 기준을 정리한다.
실제로 존재하는 구조와 앞으로 추가할 기능 영역을 구분해 설명한다.

## 전체 구조

```text
smart-desk-fin/
├── src/smart_desk/       Python FastAPI 애플리케이션
├── frontend/             React + TypeScript + Vite 대시보드
├── data/                 로컬 SQLite runtime 데이터(Git 제외)
├── tests/                Python 단위·통합 테스트
├── docs/                 설계와 구현 문서
├── pyproject.toml        Python 패키지·의존성·pytest 설정
├── .env.example          환경변수 예시
├── .gitignore            Git 제외 파일 기준
└── README.md             설치·실행·검증 진입 문서
```

MediaMTX는 기존 호스트 프로세스를 사용하고 FFmpeg는 FastAPI가 `Popen`으로
실행하므로 별도 `infra/`와 Compose 파일은 만들지 않는다.

## 루트 파일

| 경로 | 역할 |
| --- | --- |
| `README.md` | 개발 환경 설치, FastAPI·React 실행, 운영 build와 전체 검증 명령을 안내한다. |
| `pyproject.toml` | Python 버전, FastAPI·Pydantic·aiomqtt 의존성, 개발 의존성과 pytest 설정을 관리한다. |
| `.env.example` | 서버, MQTT, 책상 범위, 카메라, SQLite와 React 정적 제공 환경변수의 이름과 기본 예시를 제공한다. |
| `.gitignore` | `.env`, 가상환경, Python cache, SQLite `data/`, React `node_modules`·`dist` 등 생성 파일을 제외한다. |

로컬 실행 중 생성되는 `.venv/`, `.git/`, `.pytest_cache/`, `*.egg-info/`는
애플리케이션 소스가 아니다.

## Python 백엔드

### 최상위 애플리케이션

| 경로 | 역할 |
| --- | --- |
| `src/smart_desk/__init__.py` | Python 패키지를 선언하고 애플리케이션 버전을 제공한다. |
| `src/smart_desk/main.py` | Uvicorn이 불러오는 `app` 객체를 노출하는 최소 실행 진입점이다. |
| `src/smart_desk/media_publish.py` | Desk 서버 없이 설정에서 활성화된 카메라 publisher만 실행하는 원격 개발용 진입점이다. |
| `src/smart_desk/application.py` | 설정과 container로 FastAPI 앱을 만들고 API·React frontend를 연결한다. |
| `src/smart_desk/bootstrap.py` | 설정을 바탕으로 공유 객체를 생성하고 `AppContainer`로 조립하는 유일한 위치다. |
| `src/smart_desk/frontend.py` | Vite production build 경로를 확인하고 FastAPI SPA frontend로 등록한다. |

`main.py`에는 기능 초기화나 정책을 넣지 않는다. 새 singleton 서비스는
`bootstrap.py`에서 만들고 container에 추가한다.

### `config/`

환경에 따라 바뀌는 값과 변하지 않는 애플리케이션 상수를 관리한다.

| 경로 | 역할 |
| --- | --- |
| `src/smart_desk/config/__init__.py` | 설정 패키지를 선언한다. |
| `src/smart_desk/config/constants.py` | 앱 이름, 단일 worker 수, 물리 측정 범위 73~118cm와 제어 범위 75~115cm처럼 환경으로 넓힐 수 없는 상수를 둔다. |
| `src/smart_desk/config/settings.py` | `.env`·환경변수를 검증된 `Settings`로 한 번만 로딩해 공유한다. |

다른 파일에서 `os.getenv()`를 직접 호출하지 않는다. 새 환경변수는 적절한 하위
Settings 모델과 `.env.example`에 함께 추가한다. 단기 프로젝트에서는 별도 동적
설정 시스템을 만들지 않으며, 로드한 설정은 실행 중 변경하지 않는 규칙으로
사용한다.

### `core/`

Desk나 Vision 같은 기능과 무관한 프로세스 공통 실행 기반이다.

| 경로 | 역할 |
| --- | --- |
| `src/smart_desk/core/__init__.py` | core 패키지를 선언한다. |
| `src/smart_desk/core/container.py` | 프로세스당 하나의 `AppContainer`, singleton 설치·조회와 공유 자원 등록을 관리한다. |
| `src/smart_desk/core/lifecycle.py` | FastAPI lifespan에서 공유 자원을 지정 순서로 시작·종료한다. |
| `src/smart_desk/core/task_manager.py` | 장기 async 작업의 이름, 중복, 실패 기록과 종료 시 전체 취소를 관리한다. |
| `src/smart_desk/core/runtime.py` | `CREATED`, `READY`, `FAILED` 등 앱 실행 상태를 불변 snapshot으로 제공한다. |
| `src/smart_desk/core/logging.py` | 공통 JSON 로그 형식과 root logger를 설정한다. |
| `src/smart_desk/core/exceptions.py` | container·task 관리 등 공통 기반의 명시적 예외를 정의한다. |

기능별 오류나 상태 모델을 편의상 `core/`에 모으지 않는다. 둘 이상의 기능에서
실제로 공유하는 실행 기반만 이 폴더에 둔다.

현재 core 골조를 더 세분화하거나 별도 DI framework, process supervisor,
분산 상태 저장소를 추가하지 않는다. 실제 기능 구현에서 반복되는 책임이 확인될
때만 새 공통 계층을 만든다.

### `storage/`

프로필과 향후 기능이 함께 사용할 로컬 SQLite 실행 기반이다.

| 경로 | 역할 |
| --- | --- |
| `src/smart_desk/storage/__init__.py` | `SQLiteDatabase`와 storage 오류를 공개한다. |
| `src/smart_desk/storage/sqlite.py` | 프로젝트 루트 기준 경로, connection, transaction, 현재 version 2 migration과 schema 검증을 관리한다. |

각 operation은 worker thread에서 connection 하나를 열고 닫는다. SQL table 의미는
repository가 소유하며 storage 계층에는 범용 repository나 connection pool을 두지 않는다.

### `api/`

HTTP 요청 검증과 애플리케이션 서비스 호출을 담당한다.

| 경로 | 역할 |
| --- | --- |
| `src/smart_desk/api/__init__.py` | API 패키지를 선언한다. |
| `src/smart_desk/api/router.py` | 기능별 `APIRouter`를 하나의 최상위 router로 조립한다. |
| `src/smart_desk/api/routes/__init__.py` | HTTP route 패키지를 선언한다. |
| `src/smart_desk/api/routes/health.py` | `/health/live`, `/health/ready`와 응답 모델을 제공한다. |
| `src/smart_desk/api/routes/wled.py` | 선택 WLED의 status, capabilities와 전체 조명 control HTTP 계약을 제공한다. |

새 API는 `api/routes/<기능>.py`에 추가하고 `api/router.py`에서 연결한다. route는
함수 안에서 `get_*()`로 singleton 서비스를 조회할 수 있지만, 장치 제어 정책을
직접 구현하지 않는다.

### `modules/mqtt/`

EMQX와 통신하는 프로세스 공용 MQTT transport다. 토픽별 JSON 의미나 책상 제어
정책은 포함하지 않고, 각 기능 모듈이 등록한 handler로 수신 메시지를 전달한다.

| 경로 | 역할 |
| --- | --- |
| `src/smart_desk/modules/__init__.py` | 기능 모듈을 묶는 Python 패키지를 선언한다. |
| `src/smart_desk/modules/mqtt/__init__.py` | MQTT 공개 타입·오류와 singleton 조회 함수 `get_mqtt()`를 노출한다. |
| `src/smart_desk/modules/mqtt/client.py` | 연결·재연결, exact-topic 구독, 발행과 메시지 전달을 관리한다. |
| `src/smart_desk/modules/mqtt/models.py` | aiomqtt에 의존하지 않는 최소 수신 메시지와 handler 타입을 정의한다. |
| `src/smart_desk/modules/mqtt/topics.py` | 기존 서버·ESP32와 호환되는 MQTT 토픽 문자열을 한곳에 둔다. |

handler는 MQTT 시작 전에 등록한다. 최초 연결·구독 실패는 애플리케이션 시작
실패로 처리하고, 시작 후 단절에는 같은 process에서 자동 재연결·재구독한다.

### `modules/wled/`

| 경로 | 역할 |
| --- | --- |
| `src/smart_desk/modules/wled/models.py` | WLED snapshot, catalog와 camelCase API 모델을 정의한다. |
| `src/smart_desk/modules/wled/client.py` | HTTP JSON API, 유효 segment 전체 적용과 응답 검증을 담당한다. |
| `src/smart_desk/modules/wled/__init__.py` | 공개 타입·오류와 선택 client accessor를 제공한다. |

WLED는 enabled일 때만 lifecycle order 60으로 생성된다. 장애는 마지막 관측 snapshot에만
기록하며 Desk 안전 상태나 application readiness를 바꾸지 않는다.

### `modules/serial/`

Arduino 높이 리더의 blocking pyserial 연결을 event loop 밖에서 실행하고 완성된
bytes line과 연결 snapshot을 제공한다.

| 경로 | 역할 |
| --- | --- |
| `src/smart_desk/modules/serial/__init__.py` | 시리얼 source와 공개 snapshot 타입을 노출한다. |
| `src/smart_desk/modules/serial/source.py` | lazy open, read timeout, 단절·재연결과 안전 종료를 관리한다. |

시리얼 장치가 없어도 애플리케이션 시작을 실패시키지 않는다. open·read 오류는
`SerialSnapshot`에 기록하고 다음 설정 간격에 재연결하며, 정상 read timeout은
연결 오류로 처리하지 않는다.

### `modules/media/`

| 경로 | 역할 |
| --- | --- |
| `src/smart_desk/modules/media/__init__.py` | `WebRtcCameraPublisher`, `WebRtcFrameSource`와 최신 frame 타입 alias를 공개한다. |
| `src/smart_desk/modules/media/webrtc.py` | MediaMTX WHIP 송출과 WHEP 최신 프레임 수신을 관리한다. |

카메라별 publish와 receive 설정에 따라 필요한 클래스만 생성한다. publisher manager,
source factory, snapshot DTO와 별도 supervisor는 만들지 않는다. 원격 개발 컴퓨터는
최상위 `media_publish.py` 진입점에서 같은 `WebRtcCameraPublisher`를 재사용한다.

### `modules/desk/`

Arduino frame의 높이 의미와 ESP32 MQTT JSON 계약을 담당한다. 목표 높이와 이동
방향을 판단하고 목표·수동 이동을 관리하는 `DeskController`까지 포함한다.

| 경로 | 역할 |
| --- | --- |
| `src/smart_desk/modules/desk/__init__.py` | DeskIO 컴포넌트와 불변 상태 타입을 노출한다. |
| `src/smart_desk/modules/desk/models.py` | 방향, 높이·relay 상태 enum과 snapshot을 정의한다. |
| `src/smart_desk/modules/desk/messages.py` | 높이 발행과 ESP32 명령·상태 JSON을 검증한다. |
| `src/smart_desk/modules/desk/segment.py` | `fresh=7` mask frame을 73~118cm 높이로 순수 변환한다. |
| `src/smart_desk/modules/desk/height_monitor.py` | 최신 실제 높이·신선도를 관리하고 retained 높이를 발행한다. |
| `src/smart_desk/modules/desk/relay.py` | ESP32 UP·DOWN·STOP 발행과 live 상태 snapshot을 관리한다. |
| `src/smart_desk/modules/desk/controller.py` | 목표·HOLD·STOP, generation과 relay 상태 확인을 단독 관리한다. |

`DeskHeightMonitor`는 `SerialLineSource`를 소유한다. `DeskController`는 lifecycle
순서 30으로 MQTT 10과 monitor 20 뒤 시작하고 둘보다 먼저 final STOP을 보낸다.
`RelayClient`는 독립 runner가 없으며 command 발행 결과로 상태를 추정하지 않는다.

### `modules/profiles/`

| 경로 | 역할 |
| --- | --- |
| `src/smart_desk/modules/profiles/__init__.py` | 프로필 공개 모델·오류·repository와 `get_profiles()`를 노출한다. |
| `src/smart_desk/modules/profiles/models.py` | camelCase alias, 높이·이름·LED 검증과 update unset/null 의미를 정의한다. |
| `src/smart_desk/modules/profiles/repository.py` | `profiles` SQL, server ID 생성과 CRUD transaction을 구현한다. |

현재 프로필은 이름, 앉은·선 높이와 선택 LED 색상을 SQLite에 저장하며,
`DashboardService`와 `/api/profiles`가 공개 CRUD만 사용한다.

### `modules/assistant/`

| 경로 | 역할 |
| --- | --- |
| `src/smart_desk/modules/assistant/__init__.py` | provider-neutral Agents voice runtime 공개 surface를 제공한다. |
| `src/smart_desk/modules/assistant/agents_runtime.py` | 24kHz PCM `run_audio`, SDK VoicePipeline, session·tool 연결을 조립한다. |
| `src/smart_desk/modules/assistant/context.py` | current-user별 bounded SDK session과 실행 취소를 관리한다. |

### `modules/voice/`

| 경로 | 역할 |
| --- | --- |
| `src/smart_desk/modules/voice/models.py` | PCM format 상수, audio DTO, 상태 enum과 content-free snapshot을 정의한다. |
| `src/smart_desk/modules/voice/audio.py` | PortAudio callback queue, local output, RMS recorder와 memory WAV를 구현한다. |
| `src/smart_desk/modules/voice/wakeword.py` | `livekit-wakeword`로 `하이 스마티` ONNX의 2초 rolling 추론·reset을 담당한다. |
| `src/smart_desk/modules/voice/playback.py` | local effect와 streaming TTS PCM을 같은 speaker에 직렬 출력한다. |
| `src/smart_desk/modules/voice/service.py` | 하나의 `voice-main` task로 turn, follow-up, 오류와 aggregate lifecycle을 관리한다. |

Voice는 optional `voice` dependency extra로 설치하며 비활성 상태에서는 OpenAI,
sounddevice와 Wake Word package를 import하지 않는다. `assets/voice/effects/`에는 프로젝트가
직접 합성한 24kHz PCM16 acknowledgement/error WAV가 있고, `assets/voice/models/`에는
`하이 스마티` classifier와 provenance가 있다.

### `modules/dashboard/`

| 경로 | 역할 |
| --- | --- |
| `src/smart_desk/modules/dashboard/models.py` | Desk snapshot의 camelCase HTTP 응답과 control·target 요청 모델을 정의한다. |
| `src/smart_desk/modules/dashboard/service.py` | Desk 명령과 ProfileRepository CRUD를 유스케이스 단위로 위임한다. |
| `src/smart_desk/modules/dashboard/__init__.py` | `DashboardService`와 `get_dashboard()` accessor를 노출한다. |

### `firmware/relay-controller/`

ESP32-C3의 GPIO 3/4 active-high relay를 실행하는 FIN 전용 PlatformIO 프로젝트다.
strict MQTT parser, height session arming·lease, 방향 경계와 IRAM hardware timer ISR을
포함한다. `test/test_policy/`는 host native에서 wire와 경계 정책을 검증한다.

### `firmware/segment-reader/`

Arduino Uno 호환 CH340 보드에서 3자리 7-segment 멀티플렉싱 신호를 읽는 FIN 전용
PlatformIO 프로젝트다. 한 자리만 선택된 안정 구간에서 mask와 소수점을 반복 확인하고,
같은 후보가 세 번 연속 확인된 자리만 50ms JSON frame의 `fresh` bitset에 포함한다.
모든 표시기 관련 핀은 고임피던스 `INPUT`이며 표시기나 모터를 구동하지 않는다.

## React 대시보드

`frontend/`는 Python 패키지와 분리된 독립 Node.js 프로젝트다. 개발 시 Vite가
실행하고, 운영 시 build 결과만 FastAPI가 제공한다.

| 경로 | 역할 |
| --- | --- |
| `frontend/package.json` | React·Vite·TypeScript 버전과 `dev`, `build`, `preview` 명령을 정의한다. |
| `frontend/package-lock.json` | npm 의존성의 정확한 버전과 무결성 정보를 고정한다. 직접 수정하지 않는다. |
| `frontend/vite.config.ts` | React plugin, 개발 포트와 FastAPI `/api`·`/health` proxy를 설정한다. |
| `frontend/tsconfig.json` | 브라우저 앱과 Vite 설정용 TypeScript 프로젝트를 묶는다. |
| `frontend/tsconfig.app.json` | React 브라우저 코드의 strict TypeScript 설정을 관리한다. |
| `frontend/tsconfig.node.json` | `vite.config.ts`가 사용하는 Node.js 쪽 TypeScript 설정을 관리한다. |
| `frontend/index.html` | Vite build의 HTML 진입점과 React root 요소를 제공한다. |
| `frontend/src/main.tsx` | React root를 생성하고 전역 CSS와 최상위 `App`을 연결한다. |
| `frontend/src/App.tsx` | Desk polling, stale/error 표시와 desk·profile 화면 조합을 담당한다. |
| `frontend/src/api/dashboard.ts` | `/api` 요청 함수와 정확한 TypeScript 응답 타입을 정의한다. |
| `frontend/src/config.ts` | UI가 사용하는 75~115cm 사용자 제어 범위를 한곳에 정의한다. |
| `frontend/src/features/desk/DeskPanel.tsx` | 상태·목표와 HOLD/STOP pointer·keyboard cleanup을 제공한다. |
| `frontend/src/features/profiles/ProfilesPanel.tsx` | profile 목록·생성·수정·hard delete 확인을 제공한다. |
| `frontend/src/features/debug/DebugPanel.tsx` | Vision snapshot debug를 표시한다. browser preview와 일부 상세 근거 표시는 아직 연결하지 않았다. |
| `frontend/src/styles.css` | 대시보드의 전역·component 스타일을 정의한다. |
| `frontend/src/vite-env.d.ts` | CSS·정적 asset import에 필요한 Vite TypeScript 타입을 연결한다. |

`frontend/node_modules/`는 `npm ci`가 생성하는 로컬 의존성이고,
`frontend/dist/`는 `npm run build`가 생성하는 운영 정적 파일이다. 둘 다 Git에
저장하지 않는다.

화면이 커지면 `frontend/src` 아래에 다음처럼 책임별 폴더를 추가한다.

```text
frontend/src/
├── api/           FastAPI 요청 함수와 응답 타입
├── components/    여러 화면에서 재사용하는 UI
├── features/      책상·프로필·Vision 같은 기능별 화면과 상태
├── pages/         route 단위 화면
└── styles/        공통 token과 전역 스타일
```

초기에는 사용되지 않는 계층을 미리 만들지 않고 실제 파일이 생길 때 추가한다.

## 테스트

| 경로 | 역할 |
| --- | --- |
| `tests/conftest.py` | 각 테스트 전후에 Settings cache와 container singleton을 초기화한다. |
| `tests/unit/test_settings.py` | 환경변수 로딩, 단일 worker와 안전 범위 설정을 검증한다. |
| `tests/unit/test_container.py` | container 설치 전 접근, 동일 인스턴스 반환과 중복 설치 차단을 검증한다. |
| `tests/unit/test_lifecycle.py` | 공유 자원의 명시적 시작·안전 종료 순서를 검증한다. |
| `tests/unit/test_task_manager.py` | async 작업 중복 차단과 critical 실패 callback을 검증한다. |
| `tests/unit/test_voice_models.py` | Voice 상태 DTO의 content-free 계약을 검증한다. |
| `tests/unit/test_voice_audio.py` | callback 신호 진단과 RMS follow-up gate를 검증한다. |
| `tests/unit/test_wakeword_detector.py` | builtin model 선택, 연속 activation, reset과 close를 검증한다. |
| `tests/unit/test_playback.py` | effect/TTS 직렬화, sample carry, abort와 cancel을 검증한다. |
| `tests/unit/test_agents_voice_runtime.py` | SDK stream, lifecycle event와 cancellation 경계를 network 없이 검증한다. |
| `tests/unit/test_voice_service.py` | PCM passthrough, runtime event, follow-up, 오류와 shutdown을 검증한다. |
| `tests/unit/test_sqlite_database.py` | SQLite migration, schema, lifecycle, transaction과 cancellation을 검증한다. |
| `tests/unit/test_profile_models.py` | 프로필 alias, 범위, 정규화와 부분 수정 입력을 검증한다. |
| `tests/unit/test_profile_repository.py` | 임시 SQLite 파일에서 프로필 CRUD, 충돌, 영속성과 동시 부분 수정을 검증한다. |
| `tests/unit/test_mqtt_client.py` | broker 없이 MQTT 연결·발행·수신·재연결과 입력 검증을 확인한다. |
| `tests/unit/test_serial_source.py` | 실제 장치 없이 시리얼 open·timeout·단절·재연결·취소·종료를 검증한다. |
| `tests/unit/test_segment_decoder.py` | 완성 frame, mask·point·fresh와 73~118cm 경계를 검증한다. |
| `tests/unit/test_height_monitor.py` | 높이 snapshot, 신선도, source 오류와 retained 발행을 검증한다. |
| `tests/unit/test_relay_client.py` | ESP32 상태 검증, pulse·STOP JSON과 MQTT 오류 전파를 확인한다. |
| `tests/unit/test_desk_controller.py` | 목표·fine pulse·HOLD·watchdog·STOP race와 오류 중단을 검증한다. |
| `tests/unit/test_dashboard_service.py` | dashboard snapshot 변환과 Desk 명령 위임을 검증한다. |
| `tests/integration/test_dashboard_api.py` | Dashboard HTTP contract, 오류 상태와 SQLite profile CRUD를 검증한다. |
| `tests/integration/test_application.py` | FastAPI lifespan, health API, React 정적 제공과 SPA fallback을 검증한다. |
| `tests/integration/test_mqtt_emqx.py` | 로컬 EMQX에서 실제 QoS 1 왕복과 재연결·재구독을 선택적으로 검증한다. |
| `tests/integration/test_voice_pipeline.py` | fake 장치와 provider-neutral runtime을 연결해 wake/transcript/audio를 검증한다. |
| `tests/integration/test_wakeword_builtin.py` | Voice extra 환경에서 builtin HEY_JARVIS offline load·추론을 검증한다. |
| `tests/integration/test_voice_hardware.py` | opt-in microphone/speaker open과 local effect를 검증한다. |
| `src/smart_desk/modules/voice/debug.py` | 10000 포트 임시 Voice 관측 페이지와 read-only snapshot API를 제공한다. |

순수 상태전이와 검증은 `tests/unit/`, FastAPI·MQTT·시리얼처럼 둘 이상의 경계를
연결하는 검증은 `tests/integration/`에 둔다.

## 문서

| 경로 | 역할 |
| --- | --- |
| `docs/README.md` | 현재 구현 문서와 설계 문서의 탐색 시작점이다. |
| `docs/PROJECT_STRUCTURE.md` | 현재 폴더·파일 책임과 새 코드 배치 기준을 제공한다. |
| `docs/architecture/system-design.md` | 단일 프로세스의 전체 데이터 흐름과 계층 의존 방향을 정의한다. |
| `docs/architecture/runtime-and-concurrency.md` | singleton, lifespan, async 작업과 공유 상태 규칙을 정의한다. |
| `docs/architecture/frontend.md` | React 개발·build·FastAPI 운영 제공 방식을 설명한다. |
| `docs/architecture/component-design.md` | 앞으로 구현할 Desk·Vision·애플리케이션 클래스 책임을 정의한다. |
| `docs/architecture/desk-safety.md` | 책상 제어 상태, STOP 우선순위와 ESP32 안전 경계를 정의한다. |
| `docs/guides/README.md` | 새 작업을 계획하거나 구조를 제안할 때 읽는 판단 기준의 진입점이다. |
| `docs/guides/project-principles.md` | 단기 소규모 범위, 우선순위와 과도한 구조를 피하는 기준을 정의한다. |
| `docs/guides/design-decision-guide.md` | 클래스·모듈·async·singleton 경계를 선택하는 기준을 제공한다. |
| `docs/guides/planning-and-delivery-guide.md` | 작업 계획, 검증, 완료와 커밋 단위를 정하는 기준을 제공한다. |
| `docs/workflow/README.md` | Dashboard와 서버 기능 워크플로 문서의 탐색 진입점이다. |
| `docs/workflow/dashboard.md` | profile 설정, 메인 Dashboard와 debug 화면 흐름을 정의한다. |
| `docs/workflow/identity-and-vision.md` | 얼굴 등록·식별, 재실·자세와 서버 현재 사용자 결정을 정의한다. |
| `docs/workflow/desk-control.md` | 제어 방식, 작업 모드, 자세 자동화와 LED 흐름을 정의한다. |
| `docs/workflow/api-contracts.md` | 워크플로가 요구하는 HTTP endpoint와 오류 의미를 정의한다. |
| `docs/workflow/implementation-plan.md` | 현재 구현 차이, 순서와 검증 항목을 관리한다. |
| `docs/implementation/roadmap.md` | 2~3개월 구현 순서와 단계별 완료 조건을 정리한다. |
| `docs/tasks/README.md` | 번호가 붙은 실행 작업 문서와 현재 진행 순서를 안내한다. |
| `docs/tasks/01-*.md` ~ `09-*.md` | 앞으로 구현할 기능의 선행 조건, 개략 범위와 완료 기준을 제공한다. |

## 기능 영역

MQTT, DeskIO, Desk 제어, media와 AI Voice는 구현했으며 Vision 추론과 자동화는 아직
생성되지 않았다.
이후 기능은 다음 형태를 기본으로 하되, 실제 파일이 생기기 전 빈 폴더는 만들지
않는다.

```text
src/smart_desk/modules/
├── mqtt/          EMQX 연결, 발행·구독과 토픽 (구현 완료)
├── serial/        Arduino 시리얼 라인 수신 (구현 완료)
├── desk/          높이 해석·ESP32 명령과 목표·수동 제어 (구현 완료)
├── media/         WHIP 카메라 발행과 WHEP 최신 프레임 (구현 완료)
├── assistant/     Agents VoicePipeline runtime, user session·memory·tools
├── voice/         Wake Word·녹음·재생과 follow-up 상태 머신 (구현 완료)
├── vision/        전처리, 얼굴·자세·재실 판정
├── automation/    Vision·프로필을 이용한 목표 높이 결정
├── profiles/      프로필 모델과 SQLite 영속 저장 (구현 완료)
└── wled/          선택적 LED 장치 연동
```

기존 호스트 MediaMTX는 저장소에서 관리하지 않는다. 카메라별 `WebRtcCameraPublisher`와
`WebRtcFrameSource`는 `modules/media/`에 두고 활성화된 역할만 FastAPI lifespan에서
시작·종료한다. 원격 publisher 전용 프로세스도 자신이 시작한 WebRTC peer만 종료한다.
Python에는 MediaMTX 업로더나 관리 client를 만들지 않는다.

기능 클래스의 필드와 메서드는 [컴포넌트 설계](architecture/component-design.md),
책상 제어 구현은 [책상 제어와 안전](architecture/desk-safety.md)을 먼저 따른다.

## 파일 배치 판단 기준

| 새 코드 | 위치 |
| --- | --- |
| 환경변수와 검증 | `config/settings.py` |
| 앱 전체 공통 실행 기반 | `core/` |
| 기능 정책·장치 처리 | `modules/<기능>/` |
| HTTP route | `api/routes/` |
| singleton 객체 생성과 연결 | `bootstrap.py` |
| React API 호출·화면 | `frontend/src/` |
| 순수 정책 테스트 | `tests/unit/` |
| 경계 연결 테스트 | `tests/integration/` |

폴더나 파일 책임이 바뀌면 이 문서와 해당 아키텍처 문서를 함께 갱신한다.
