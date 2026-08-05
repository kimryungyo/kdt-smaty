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

기능 컴포넌트가 추가되면 `bootstrap.py`에서 생성하고 `AppContainer`에 등록한다.

## 단기 프로젝트 실행 기준

- Uvicorn worker는 하나만 사용한다. 별도 process lock이나 전용 supervisor는 두지 않는다.
- HTTP·MQTT·책상 제어의 I/O 동시성은 같은 프로세스의 `asyncio` task로 처리한다.
- 카메라 읽기·YOLO처럼 blocking 또는 연산이 무거운 작업만 `asyncio.to_thread()`로 넘긴다.
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
    desk: DeskController
    height_monitor: DeskHeightMonitor
    vision: VisionStateService
    profiles: ProfileRepository
    mqtt: MqttService

def get_desk() -> DeskController: ...
def get_vision() -> VisionStateService: ...
```

위 기능 필드는 향후 구현 형태다. 현재 container에는 설정, runtime,
`TaskManager`와 lifecycle resource 목록만 있다. container가 직접 `start()`나
`shutdown()`을 제공하지 않으며 `core/lifecycle.py`가 등록된 자원의 수명주기를
관리한다.

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
시작: 설정 → MQTT → 높이 센서 → 릴레이 상태 확인 → Desk 제어 → 카메라 → Vision → API 제공
종료: 새 요청 차단 → 자동화 중지 → Desk STOP → 작업 취소/대기 → MQTT/시리얼/카메라 해제
```

초기화 실패 시 HTTP 서버만 남긴 채 제어 루프를 계속 실행하지 않는다. 특히
Desk 시작이 실패하거나 센서 상태가 불명확하면 릴레이 STOP을 먼저 시도한다.

## 장기 실행 작업

| 작업 | 실행 방식 | 최신 상태 |
| --- | --- | --- |
| MQTT 수신 | async 네트워크 루프 | 서비스/장치 상태 |
| 높이 수신 | async 시리얼 또는 전용 thread | `HeightSnapshot` |
| 카메라 캡처 | 전용 thread 또는 비차단 어댑터 | `FrameSnapshot` |
| 전처리 | async 주기 작업 | 전처리 프레임 |
| YOLO·얼굴 추론 | `asyncio.to_thread()` 또는 executor | detector 결과 |
| Desk 제어 | async 주기 작업 | `DeskSnapshot` |
| 자동화 | 상태 변경 이벤트 또는 짧은 주기 작업 | 자동화 상태 |

OpenCV의 `read()`와 YOLO 추론을 이벤트 루프에서 직접 실행하면 HTTP 요청과
STOP 처리가 늦어질 수 있다. 이 작업들은 executor로 넘기고, 제어 루프는 짧게
끝나도록 유지한다.

## 공유 상태 규칙

- 갱신 작업만 내부 가변 상태를 쓴다.
- 외부 소비자는 불변 snapshot을 받는다.
- snapshot 교체는 짧은 `asyncio.Lock`으로 보호한다.
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
루프의 `finally`와 `stop()`에서 ESP32 STOP을 보장한다.
