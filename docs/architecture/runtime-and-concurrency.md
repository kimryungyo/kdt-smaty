# 실행과 동시성

단일 Python 프로세스에서 FastAPI와 장기 실행 작업을 안전하게 운영하는 기준이다.
`asyncio`는 I/O 동시성을 위한 도구이며, CPU/GPU 추론을 자동으로 비동기로 만들지
않는다.

## 현재 구현

- `src/smart_desk/application.py`: FastAPI app factory와 container 설치
- `src/smart_desk/bootstrap.py`: 설정으로 공유 객체 조립
- `src/smart_desk/core/container.py`: singleton container와 공유 자원 등록
- `src/smart_desk/core/lifecycle.py`: 공유 자원 시작·종료와 FastAPI lifespan
- `src/smart_desk/core/task_manager.py`: 이름 기반 async 작업과 critical 실패 기록
- `src/smart_desk/storage/sqlite.py`: SQLite migration·transaction과 blocking I/O 경계
- `src/smart_desk/modules/profiles/`: 프로필 모델과 SQLite CRUD
- `src/smart_desk/modules/mqtt/client.py`: EMQX 연결·재연결과 메시지 전달
- `src/smart_desk/modules/serial/source.py`: Arduino 시리얼 lazy open과 재연결
- `src/smart_desk/modules/desk/height_monitor.py`: 높이 수신·신선도와 MQTT 발행
- `src/smart_desk/modules/desk/relay.py`: ESP32 명령·상태 계약
- `src/smart_desk/modules/desk/controller.py`: 목표·HOLD·STOP 상태전이와 pulse runner
- `src/smart_desk/modules/assistant/`: Agents SDK Voice runtime, current-user session context,
  function tool, profile memory와 Assistant turn store
- `src/smart_desk/modules/voice/`: microphone callback queue, Wake Word와 voice 상태 머신

`SQLiteDatabase`는 `bootstrap.py`에서 생성해 lifecycle order 5로 가장 먼저 등록한다.
그 뒤 `MqttClient` 10, `DeskHeightMonitor` 20, `DeskController` 30 순서로 시작한다.
종료 시에는 controller가 final STOP을 보낸 뒤 monitor와 MQTT를 종료하고 마지막에
SQLite operation을 닫는다.

Voice가 활성화되면 identity 70, Assistant context/turn 75/76, automation 80 뒤 lifecycle
order 90에 aggregate `VoiceService` 하나를
등록한다. 내부 시작은 Wake Word detector → playback → microphone → non-critical
`voice-main` 순서다. 종료에서는 새 입력을 막고 현재 turn과 speaker buffer를 취소한 뒤
microphone, detector, speaker와 OpenAI client를 닫으므로 Desk STOP과 media 종료보다
먼저 끝난다. Voice 장치 시작 실패는 service 내부 `ERROR`로 처리해 애플리케이션
readiness를 내리지 않는다. WLED와 Voice는 선택 기능이며 `enabled=false`는 정상
`DISABLED`다. 활성화한 기능의 정적 구성 오류는 명시적으로 실패시키고 runtime 단절은 해당
기능 snapshot으로 표현한다.

## 단기 프로젝트 실행 기준

- Uvicorn worker는 하나만 사용한다. 별도 process lock이나 전용 supervisor는 두지 않는다.
- HTTP·MQTT·책상 제어의 I/O 동시성은 같은 프로세스의 `asyncio` task로 처리한다.
- FFmpeg는 카메라별 `Popen` 자식 process로 실행하고, RTSP 프레임 읽기·YOLO처럼
  blocking 또는 연산이 무거운 작업만 전용 thread나 `asyncio.to_thread()`로 넘긴다.
- FastAPI 의존성 주입 framework를 추가하지 않고 route 안에서 `get_*()`를 호출한다.
- 설정은 시작 시 한 번 읽으며 실행 중 갱신 기능을 만들지 않는다.

worker 하나는 처리 프로세스가 하나라는 뜻이지, async 작업을 하나만 실행한다는
뜻이 아니다. worker를 늘리면 container·MQTT·카메라·책상 제어 객체도 프로세스별로
복제되므로 이 프로젝트에서는 사용하지 않는다. 실제 책상 연결 중에는 자동
재시작을 일으키는 Uvicorn `--reload`도 사용하지 않는다.

## AppContainer와 singleton

`AppContainer`는 프로세스 시작 시 한 번 생성된다. `get_desk()` 같은 함수는
컨테이너에 생성된 같은 객체를 반환한다.

```python
class AppContainer:
    settings: Settings
    runtime: RuntimeState
    task_manager: TaskManager
    database: SQLiteDatabase
    profiles: ProfileRepository
    mqtt: MqttClient
    height_monitor: DeskHeightMonitor
    relay: RelayClient
    desk: DeskController

    vision: VisionService
    current_user: CurrentUserSessionService
    automation: AutomationService
    assistant_turns: AssistantTurnStore

def get_desk() -> DeskController: ...
def get_vision() -> VisionService: ...
def get_mqtt() -> MqttClient: ...
```

현재 container에는 설정, runtime, `TaskManager`, `SQLiteDatabase`, profile/activity-mode
repository, `MqttClient`, 높이 monitor, relay adapter, `DeskController`, Vision, current-user,
automation과 Assistant turn store 및 lifecycle resource 목록이 있다. container가 직접
`start()`나 `shutdown()`을 제공하지 않으며
`core/lifecycle.py`가 등록된 자원의 수명주기를 관리한다.

FastAPI route와 MQTT handler 같은 진입점은 함수 내부에서 `get_*()`를 직접
호출한다. `Depends` 사용은 필수가 아니다. 반면 `DeskController` 같은 핵심
서비스가 내부에서 다른 singleton을 찾지 않도록, 서비스 간 의존성은
`bootstrap.py`에서 생성자로 전달한다.

```text
route / event handler → get_desk() 허용
DeskController 내부  → 생성자로 받은 HeightMonitor·RelayClient 사용
```

모듈 import 시점에는 `get_*()`를 호출하지 않는다. FastAPI lifespan이 시작된 뒤
요청이나 작업 안에서만 접근한다.

이 접근은 FastAPI 라우트, MQTT 핸들러, 자동화 작업이 같은 프로세스에 있을 때만
사용한다. 향후 프로세스를 나누면 `get_desk()`는 각 프로세스의 별도 인스턴스를
가리키므로 앱 간 통신 수단으로 사용할 수 없다.

## 시작과 종료 순서

FastAPI lifespan에서 컨테이너를 시작하고 종료한다. 개별 장기 작업은 중앙
`TaskManager`에 이름과 critical 여부를 지정해 등록한다.

```text
사전 인프라: EMQX → 호스트 MediaMTX
애플리케이션 시작: 설정 → SQLite 검증 → MQTT runner 시작(offline 재시도 가능) → 높이 센서 → 릴레이 상태 확인 → Desk 제어 → WHIP publisher → WHEP 최신 프레임 입력 → Vision → API 제공
애플리케이션 종료: 새 요청 차단 → 자동화 중지 → Desk STOP → 작업 취소/대기 → WHEP reader 종료 → WHIP publisher 종료 → MQTT/시리얼 해제 → SQLite 종료
```

초기화 실패 시 HTTP 서버만 남긴 채 제어 루프를 계속 실행하지 않는다. 특히
Desk 시작이 실패하거나 센서 상태가 불명확하면 릴레이 STOP을 먼저 시도한다.
SQLite 시작이 실패하면 MQTT와 Desk는 시작하지 않고 readiness를 올리지 않는다.

MQTT runner는 최초 연결 또는 등록 토픽 구독의 `aiomqtt.MqttError`도 실행 중 단절처럼
재연결 interval 뒤 반복한다. `start()`는 broker가 준비됐을 때 연결·전체 구독 완료를 기다리는
호환성을 유지하되, 첫 transient 실패나 시작 대기 시간 초과에는 runner를 취소하지 않고
disconnected 상태로 lifecycle을 계속한다. `is_connected()`는 전체 구독 완료 전 `false`이고
publish는 fail-closed다. 반대로 설정·프로그래밍 오류는 runner를 종료해 `TaskManager`의 기존
critical failure 처리를 따른다.

SQLite의 동기 API는 각 operation마다 `asyncio.to_thread()`로 event loop 밖에서
실행한다. 하나의 `asyncio.Lock`이 read/write와 종료를 직렬화하며, 호출 coroutine이
취소돼도 worker의 commit 또는 rollback과 connection close가 끝날 때까지 lock을
유지한다. connection은 operation마다 worker thread 안에서 열고 닫는다.

운영 relay transport는 Wi-Fi/MQTT이며 serial bridge resource를 시작·종료 순서에 추가하지
않는다. Arduino 높이 USB serial은 센서 입력이므로 별도 연결로 유지한다.

전역 readiness는 모든 API의 공통 권한 검사가 아니다. profile CRUD와 상태 조회는 관련
service가 준비되면 응답하고, 실제 이동은 명령 지점에서 fresh height, MQTT와 ESP32 relay
상태를 각각 확인한다. WLED·Voice 장애는 해당 기능만 degraded다.

## 장기 실행 작업

| 작업 | 실행 방식 | 최신 상태 |
| --- | --- | --- |
| MQTT 수신 | async 네트워크 루프 | 서비스/장치 상태 |
| 높이 수신 | async 시리얼 또는 전용 thread | `HeightSnapshot` |
| 카메라 발행 | 카메라별 aiortc WHIP peer | 연결 여부 |
| WHEP 프레임 수신 | 카메라별 aiortc peer/task | 최신 `(image, captured_at)` 또는 `None` |
| workspace 최신 프레임 | V4L2/PyAV 전용 thread | 압축 JPEG 한 장 또는 `None` |
| 전처리 | async 주기 작업 | 전처리 프레임 |
| YOLO·얼굴 추론 | `asyncio.to_thread()` 또는 executor | detector 결과 |
| Desk 제어 | async 주기 작업 | `DeskSnapshot` |
| AI 음성 | `voice-main` async task + PortAudio callback | `VoiceSnapshot` |
| 자동화 | 상태 변경 이벤트 또는 짧은 주기 작업 | 자동화 상태 |

WHEP decode와 YOLO 추론이 HTTP 요청과 STOP 처리를 늦추지 않게 peer 수신은 독립 task,
추론은 executor에서 수행한다. 물리 웹캠은 `WebRtcCameraPublisher`가 소유한다.
`WebRtcFrameSource.stop()`은 WHEP peer를 닫고 lifecycle이 그 뒤 publisher를 종료해
물리 장치를 해제한다.

## 공유 상태 규칙

- 갱신 작업만 내부 가변 상태를 쓴다.
- 연결 여부 같은 단순 값은 동기 메서드로, 복합 상태는 불변 snapshot으로 받는다.
- 같은 event loop 안의 snapshot 교체는 짧은 `asyncio.Lock`, 카메라 thread와
  공유하는 최신 프레임은 짧은 `threading.Lock`으로 보호한다.
- I/O, 추론, MQTT publish를 lock 안에서 기다리지 않는다.
- `DeskController`는 별도 command lock으로 명령 상태전이를 직렬화한다.

프레임은 `Queue`에 무제한으로 넣지 않는다. 처리 지연 시에도 최신 프레임 하나만
남기는 방식이 실시간 제어에 적합하다.

## 명령 우선순위

`STOP`은 새 목표, 수동 HOLD, 자동화 명령보다 우선한다. 목표 이동 중 새 목표가
오면 기존 동작을 STOP한 뒤 새 목표를 평가한다. API, MQTT, 자동화 작업은 모두
`DeskController`의 공개 메서드만 호출한다.

critical 작업이 예기치 않게 종료되면 현재 `TaskManager`는 애플리케이션을
`FAILED`로 바꿔 readiness를 내린다. generic shutdown coordinator나 별도 process
supervisor는 기본 골조에 추가하지 않는다. Desk 컴포넌트를 구현할 때 해당 제어
루프의 `finally`, 수명주기 `stop()`과 명령용 `stop_motion()`에서 ESP32 STOP을
보장한다.

## I/O와 상태 조회 시그니처

실제 대기나 네트워크 I/O가 있는 `start()`, `stop()`, `publish()`,
`read_line()`은 async로 둔다. 이미 메모리에 교체된 불변 상태를 읽는
`get_snapshot()`, `get_latest_frame()`과 MQTT 연결 여부를 읽는 `is_connected()`는
동기 메서드로 둔다. getter 안에서 네트워크 요청이나 재접속을 수행하지 않는다.

`DeskController.stop()`은 제어 루프의 수명주기 종료에만 사용한다. 사용자의
정지 명령과 안전 중단은 `stop_motion(reason)`으로 구분한다. `RelayClient`는
독립 실행 루프를 소유하지 않으므로 수명주기 메서드를 두지 않고 ESP32 명령인
`send_stop()`만 제공한다.
