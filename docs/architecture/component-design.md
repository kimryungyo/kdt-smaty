# 컴포넌트 설계

클래스마다 한 가지 상태와 책임을 두는 기준이다. 모든 메서드 구현은 초안이며,
공개 API는 구현 과정에서 테스트와 함께 확정한다.

## 기반 어댑터

### `MqttClient`

EMQX 연결, 재연결, 발행·구독과 연결 상태만 관리한다. MQTT JSON 의미나 책상
정책을 해석하지 않으며, 인증을 사용하지 않는 현재 범위에서는 host, port,
client ID와 keepalive만 설정한다.

```python
class MqttClient:
    def register_handler(
        self,
        topic: str,
        handler: MessageHandler,
        *,
        qos: Qos = 1,
    ) -> None: ...

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def publish(
        self,
        topic: str,
        payload: bytes | str,
        *,
        qos: Qos = 1,
        retain: bool = False,
    ) -> None: ...
    def is_connected(self) -> bool: ...
```

handler는 bootstrap에서 등록하고 재연결할 때 자동으로 다시 구독한다. `retain`은
broker가 토픽의 마지막 메시지를 보관해 새 구독자에게 전달하는 옵션이다. 오래된
명령이 다시 전달되지 않도록 기본값은 `False`로 두며, clean session과는 별개로
취급한다. payload JSON 검증은 각 기능의 `messages.py`가 담당하고 `MqttClient`는
`dict`를 직접 받지 않는다. 연결 여부와 마지막 오류는 `_connected`,
`_last_error` 내부 필드로만 관리하고 별도 `MqttSnapshot`은 만들지 않는다. 다른
객체는 필요할 때 `is_connected()`로 연결 여부만 확인한다.

### `SerialLineSource`

Arduino 시리얼 포트를 단독으로 열고 완성된 바이트 라인을 비동기로 제공한다.
세그먼트 해석과 높이 범위 검증은 담당하지 않는다.

```python
class SerialLineSource:
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def read_line(self, timeout_seconds: float | None = None) -> bytes: ...
    def get_snapshot(self) -> SerialSnapshot: ...
```

## 책상 컴포넌트

### `SegmentDecoder`

Arduino가 보내는 원시 세그먼트 데이터 또는 JSON을 높이 값으로 해석하는 순수
변환기다. 자체 루프와 가변 상태를 두지 않는다.

```python
class SegmentDecoder:
    def decode(self, raw_message: bytes | str) -> float | None: ...
```

| 항목 | 역할 |
| --- | --- |
| `decode(raw_message)` | 원시 메시지를 해석해 유효한 높이(cm)를 반환한다. 형식 또는 범위가 유효하지 않으면 `None`을 반환한다. |

### `DeskHeightMonitor`

시리얼에서 최신 유효 높이를 읽고 신선도를 관리한다. 센서의 단일 소유자다.

```python
class DeskHeightMonitor:
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def get_snapshot(self) -> HeightSnapshot: ...
```

| 필드 또는 메서드 | 역할 |
| --- | --- |
| `current_height_cm` | 마지막으로 확인한 유효 높이다. 측정값이 없으면 `None`이다. |
| `observed_at` | 현재 높이를 받은 시각이다. 신선도와 통신 단절을 판단하는 근거가 된다. |
| `sensor_status` | `ONLINE`, `STALE`, `ERROR`처럼 센서 값을 제어에 사용 가능한지 나타낸다. |
| `start()` | `SerialLineSource`의 수신 작업을 시작하고 최신 높이를 계속 갱신한다. |
| `stop()` | 높이 수신 작업을 중지하고 장치 연결을 안전하게 해제한다. |
| `get_snapshot()` | 높이·수신 시각·센서 상태를 불변 `HeightSnapshot`으로 반환한다. |

`HeightSnapshot`에는 `height_cm`, `observed_at`, `status`를 포함한다. 값이
있더라도 오래됐으면 `status=STALE`로 표시하며, 제어기는 이를 새 측정값으로
취급하지 않는다.

### `RelayClient`

ESP32 릴레이 제어 프로토콜만 담당한다. `UP`, `DOWN`, `STOP`을 전송하지만
목표 높이와 이동 방향을 자체 판단하지 않는다.

```python
class RelayClient:
    async def handle_status(self, message: MqttMessage) -> None: ...
    async def pulse(self, direction: Direction, hold_ms: int) -> None: ...
    async def send_stop(self) -> None: ...
    def get_snapshot(self) -> RelaySnapshot: ...
```

| 필드 또는 메서드 | 역할 |
| --- | --- |
| `handle_status(message)` | MQTT에서 받은 ESP32 상태를 검증해 최신 릴레이 상태로 갱신한다. handler 등록은 bootstrap이 담당한다. |
| `pulse(direction, hold_ms)` | 검증된 방향과 짧은 유지 시간을 ESP32에 전달한다. 목표나 안전 정책은 판단하지 않는다. |
| `send_stop()` | ESP32에 즉시 정지 명령을 전송한다. 호출자는 이 요청이 실제로 도달했는지 상태 메시지로 별도 확인한다. |
| `get_snapshot()` | 마지막 ESP32 상태·수신 시각·오류를 불변 snapshot으로 반환한다. |

`RelayClient`는 독립 실행 루프나 연결을 소유하지 않으므로 `start()`와 `stop()`을
두지 않는다. MQTT 연결·구독 수명주기는 `MqttClient`가 관리하고, 애플리케이션
종료 시 안전 STOP은 먼저 종료되는 `DeskController.stop()`이 요청한다.

같은 방향 pulse를 만료 전에 갱신하면 ESP32는 릴레이를 다시 OFF→ON하지 않고
자동 종료 시각만 연장한다. 갱신 주기와 연속 이동 여부는 `RelayClient`가 아니라
`DeskController`가 결정한다.

### `DeskController`

목표 이동·수동 HOLD·정지의 상태전이를 단독으로 관리한다. `DeskHeightMonitor`와
`RelayClient`를 사용하지만, 다른 어떤 객체도 `RelayClient`를 직접 사용하지
않는다. 상세 안전 규칙은 [책상 제어와 안전](desk-safety.md)을 따른다.

```python
class DeskController:
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def stop_motion(self, reason: str = "") -> None: ...
    async def set_target(self, height_cm: float) -> None: ...
    async def increase_target(self, amount_cm: float) -> None: ...
    async def decrease_target(self, amount_cm: float) -> None: ...
    async def hold_up(self) -> None: ...
    async def hold_down(self) -> None: ...
    def get_snapshot(self) -> DeskSnapshot: ...
```

| 필드 또는 메서드 | 역할 |
| --- | --- |
| `target_height_cm` | 자동 목표 이동의 목적 높이다. 수동 제어·정지·오류 상태에서는 `None`이 될 수 있다. |
| `state` | `IDLE`, `MOVING`, `MANUAL`, `STOPPED`, `ERROR` 등 현재 제어 상태를 보관한다. |
| `direction` | 현재 또는 마지막 이동 방향(`UP`, `DOWN`)이다. 정지 상태에서는 `None`이다. |
| `start()` | 목표 이동 판단, 수동 watchdog, 센서 신선도 검사를 수행하는 제어 작업을 시작한다. |
| `stop()` | 제어 루프를 종료하는 수명주기 메서드다. 종료 전에 진행 중인 이동을 안전하게 정지한다. |
| `stop_motion(reason)` | 진행 중인 목표·수동 이동만 취소하고 ESP32 STOP을 요청한다. `reason`은 상태와 로그에 남긴다. |
| `set_target(height_cm)` | 목표 높이를 검증해 설정하고, 제어 루프가 현재 높이와 비교해 이동하도록 한다. |
| `increase_target(amount_cm)` | 현재 목표를 지정 값만큼 높인다. 결과가 제어 상한 115cm를 넘으면 거부한다. |
| `decrease_target(amount_cm)` | 현재 목표를 지정 값만큼 낮춘다. 제어 범위를 벗어나는 결과는 거부한다. |
| `hold_up()` / `hold_down()` | 대시보드 버튼을 누르는 동안 반복 호출해 수동 이동과 watchdog 시각을 갱신한다. 호출이 끊기면 정지한다. |
| `get_snapshot()` | 현재 높이, 목표, 상태, 방향, 오류·수신 시각을 묶은 불변 `DeskSnapshot`을 반환한다. |

`set_target()`은 목표를 바꾸고 제어 루프를 깨운다. 호출자가 릴레이 펄스를
직접 전송하거나 이동 루프를 중복 생성하지 않는다.

단기 구현에서는 `start_manual()`과 `refresh_manual_hold()` 같은 추가 API를
나누지 않는다. 브라우저가 버튼을 누르는 동안 `hold_up()` 또는 `hold_down()`을
주기적으로 요청하고, 버튼을 놓으면 `stop_motion()`을 호출한다.

`SegmentDecoder`는 73~118cm 측정 범위만 유효한 높이로 해석하고,
`DeskController`는 75~115cm 제어 범위를 벗어난 목표와 이동을 허용하지 않는다.
활성 목표가 없는 증감 요청은 거부하며, 진행 중 잘못된 대체 목표는 기존 이동을
먼저 STOP한 뒤 입력 오류를 반환한다. 첫 FIN 구현은
`smartdesk-fin-relay-1.0.0` firmware 상태만 이동 admission에 사용한다.

## 영상 컴포넌트

### `CameraPublisher`

물리 카메라 하나를 FFmpeg 자식 process 하나로 열어 호스트 MediaMTX의 고정 RTSP
경로에 발행한다. 사용자 카메라와 자세 카메라는 같은 클래스를 각각 한 번 생성한다.

```python
class CameraPublisher:
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def is_running(self) -> bool: ...
```

`start()`는 인자 목록과 `shell=False`로 `Popen`을 실행한다. `stop()`은 자신이 만든
process에만 `terminate`, 제한 시간 `wait`, 필요 시 `kill`을 적용한다. 여러
publisher를 감싸는 manager, factory와 별도 supervisor는 두지 않는다.

### `RtspFrameSource`

MediaMTX의 카메라 경로 하나에 연결해 재연결과 최신 프레임 수집을 담당한다.
물리 웹캠은 `CameraPublisher`가 실행한 FFmpeg만 열며 `RtspFrameSource`는
`/dev/video*`를 직접 열지 않는다. 최신 프레임은 이미지와 `time.monotonic()`
캡처 시각의 튜플로 보관한다.

```python
class RtspFrameSource:
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def get_latest_frame(self) -> tuple[np.ndarray, float] | None: ...
    def is_connected(self) -> bool: ...
    def get_last_error(self) -> str | None: ...
```

| 필드 또는 메서드 | 역할 |
| --- | --- |
| `latest_frame` | 가장 최근 이미지와 캡처 시각이다. 이전 프레임은 보관하지 않는다. |
| `start()` | RTSP 읽기 작업을 시작한다. MediaMTX 또는 publisher 연결이 끊기면 정해진 정책으로 재연결한다. |
| `stop()` | RTSP 읽기 thread를 중지하고 스트림 연결을 해제한다. publisher와 MediaMTX는 종료하지 않는다. |
| `get_latest_frame()` | 최신 이미지와 캡처 시각을 반환한다. 아직 프레임이 없거나 단절되면 `None`이다. |
| `is_connected()` | 현재 reader 연결 여부를 반환한다. |
| `get_last_error()` | 마지막 연결·read 오류 문자열 또는 `None`을 반환한다. |

사용자 카메라와 자세 카메라는 같은 클래스를 각각 한 번 생성한다. 프레임을
누적하지 않고 최신 프레임 하나만 교체하며 소비자는 반환된 이미지를 수정하지
않는다. `FrameSnapshot`, `CameraSnapshot`과 `FrameSource` Protocol은 실제 소비
요구가 생길 때까지 만들지 않는다.

### FFmpeg와 MediaMTX

FFmpeg는 `CameraPublisher`가 관리하는 FastAPI 자식 process이고, MediaMTX는 이미
호스트에서 실행 중인 외부 인프라다. FastAPI lifecycle은 자신의 FFmpeg만
시작·종료하고 MediaMTX는 건드리지 않는다. 초기 구현에는 Docker·Compose,
`MediaMtxUploader`, MediaMTX client 또는 Python 프레임 업로드 메서드를 추가하지
않는다.

### `FramePreprocessor`

원본 프레임을 정해진 주기로 읽어 크기 조절, crop, 색공간 변환처럼 공통
전처리를 수행한다. 카메라 연결이나 모델 추론은 담당하지 않는다.

| 필드 또는 메서드 | 역할 |
| --- | --- |
| `latest_frame` | 모델 입력에 맞게 변환한 최신 프레임이다. 원본 프레임과 같은 순번을 기록한다. |
| `interval_seconds` | 전처리 주기를 정한다. 카메라 프레임마다 처리하지 않아도 되는 작업의 부하를 제한한다. |
| `start()` / `stop()` | 전처리 반복 작업을 시작하거나 취소한다. |
| `get_latest_frame()` | 가장 최근의 전처리 결과를 반환한다. 결과가 아직 없으면 `None`이다. |

### `FaceRecognizer`, `PostureDetector`, `PresenceDetector`

각각 신원, 자세, 재실이라는 하나의 판정을 맡는다. 무거운 추론은 executor에서
수행하고, 최신 결과와 관측 시각만 보관한다.

| 필드 또는 메서드 | 역할 |
| --- | --- |
| `latest_result` | 각 detector가 마지막으로 확정한 판정과 신뢰도·관측 시각이다. |
| `model` | YOLO, 얼굴 임베딩 모델처럼 추론에 필요한 단일 모델 인스턴스다. |
| `start()` / `stop()` | 정해진 주기로 최신 프레임을 처리하는 작업을 시작하거나 중지한다. |
| `get_snapshot()` | 신원·자세·재실 중 해당 detector의 불변 결과 snapshot을 반환한다. |

### `VisionStateService`

세 detector 결과를 통합해 자동화에 사용할 `VisionSnapshot`을 만든다. 이곳에서
`RECOGNIZED`, `UNREGISTERED`, `UNKNOWN`, `SITTING`, `STANDING`, `VACANT` 등의
안정화 규칙을 적용한다.

| 필드 또는 메서드 | 역할 |
| --- | --- |
| `latest_snapshot` | 자동화가 사용할 통합 신원·자세·재실 상태와 마지막 관측 시각이다. |
| `start()` / `stop()` | detector 결과를 통합·안정화하는 작업을 시작하거나 중지한다. |
| `get_snapshot()` | 최신 `VisionSnapshot`을 반환한다. 상태가 오래됐으면 자동화가 안전하게 중단할 수 있도록 시각을 포함한다. |

## 애플리케이션 컴포넌트

### `AssistantService`와 `OpenAiGateway`

`OpenAiGateway`는 `AsyncOpenAI(max_retries=0)` 하나로 memory WAV file transcription,
Responses structured output와 24kHz PCM TTS stream을 제공한다. `store=False` request 뒤
반환된 reasoning·message output item 전체를 JSON-compatible dict로 바꾼다.

`AssistantService`는 고정 `voice:local` session과 `asyncio.Lock`을 소유한다. Responses가
성공한 경우에만 현재 user item과 전체 output item을 한 번에 commit하고, 실패·timeout·
cancel에서는 기존 history를 유지한다. `session_max_turns`는 성공 history 크기만 제한하며
Wake Word 없는 follow-up 횟수를 제한하지 않는다.

### `VoiceService`

`VoiceService`는 `LocalAudioInput`, `LiveKitWakeWordOnnxDetector`, `RmsRecorder`,
`AssistantService`와 `PlaybackCoordinator`를 연결해 `WAITING_WAKE`, `RECORDING`,
`PROCESSING`, `SPEAKING`, `WAITING_FOLLOWUP` 상태를 하나의 `voice-main` task에서 순차
진행한다. callback thread는 80ms PCM을 event loop의 bounded queue로 전달하기만 한다.

Voice는 half-duplex다. TTS 중 입력을 폐기하고 정상 drain 뒤 250ms guard를 거쳐 4초
follow-up을 연다. 정상 후속 응답마다 새 4초 창을 만들며 횟수 제한은 없다. microphone,
speaker와 Wake Word 오류만 Voice `ERROR`이고 OpenAI turn 오류는 local error effect 뒤
`WAITING_WAKE`로 복귀한다.

### `SQLiteDatabase`와 `ProfileRepository`

`SQLiteDatabase`는 프로젝트 루트 기준 `data/smart_desk.db` 경로, version migration,
connection 설정과 transaction만 담당한다. SQLite 동기 작업은 `asyncio.to_thread()`로
실행하고 프로세스 안의 read/write를 하나의 lock으로 직렬화한다. 현재 version 1에는
`profiles` 테이블 하나만 있으며 손상·새 version·schema 불일치 DB를 자동 수선하거나
초기화하지 않는다.

`ProfileRepository`는 `profiles` SQL과 row/Pydantic 변환을 소유한다. 상위 서비스는
SQLite connection이나 schema에 접근하지 않고 아래 공개 CRUD만 사용한다.

```python
async def list_profiles() -> list[Profile]: ...
async def get_profile(profile_id: str) -> Profile: ...
async def create_profile(create: ProfileCreate) -> Profile: ...
async def update_profile(profile_id: str, update: ProfileUpdate) -> Profile: ...
async def delete_profile(profile_id: str) -> None: ...
```

부분 수정은 전달된 column만 같은 transaction에서 갱신하며 `ledColor: null`만
명시적인 값 제거로 허용한다. repository는 lifecycle resource가 아니고 container가
소유한 `SQLiteDatabase`를 생성자로 받는다.

| 클래스 | 책임 |
| --- | --- |
| `ProfileRepository` | 프로필·높이·LED 설정의 읽기와 영속 저장 |
| `DashboardService` | FastAPI 요청에 맞는 유스케이스 호출과 상태 조합 |
| `AutomationService` | Vision·프로필·Desk 상태를 읽고 목표 설정 또는 STOP 결정 |
| `MqttClient` | 외부 MQTT 메시지 구독·발행과 연결 상태 관리 |
| `AppContainer` | `bootstrap.py`가 만든 공유 객체를 한곳에 보관하는 singleton 접근점 |

`DashboardService`는 `DeskController`를 직접 사용할 수 있다. 단, 자동 높이
결정 규칙은 API 라우트나 DashboardService에 흩어놓지 않고
`AutomationService`에 둔다.

| 클래스 | 핵심 필드 또는 메서드 | 역할 |
| --- | --- | --- |
| `ProfileRepository` | `list_profiles()`, `get_profile()`, `create_profile()`, `update_profile()`, `delete_profile()` | 프로필의 읽기·검증·SQLite 영속 저장을 한곳에서 수행한다. |
| `DashboardService` | `get_status()`, `set_manual_control()`, `set_target()` | HTTP 요청을 유스케이스 호출로 바꾸고 화면용 통합 상태를 만든다. |
| `AutomationService` | `evaluate()` | Vision·프로필·Desk snapshot을 읽어 목표 설정 또는 STOP 여부를 판단한다. |
| `MqttClient` | `start()`, `stop()`, `publish()`, `register_handler()` | MQTT 연결과 토픽별 수신 handler 등록을 관리한다. 하드웨어 정책은 포함하지 않는다. |
| `AppContainer` | 기능별 객체 필드 | 생성된 singleton 객체를 보관한다. 객체 생성은 `bootstrap.py`, 시작·종료는 `core/lifecycle.py`, 조회는 모듈의 `get_*()` 함수가 담당한다. |
