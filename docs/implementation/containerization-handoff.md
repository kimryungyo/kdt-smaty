# Docker 배포·분산 Vision 상세 설계 인수인계

## 문서 목적

이 문서는 `smart-desk-fin`을 Docker로 배포하고, 처음에는 메인 서비스와 영상 처리를
같은 호스트에서 실행하다가 필요할 때 영상 처리만 다른 호스트로 옮길 수 있도록 상세
설계를 진행할 다음 작업자에게 현재 맥락과 결정 사항을 전달한다.

이 문서는 최종 Docker 설계서나 구현 완료 보고서가 아니다. 다음 작업자는 아래 확정
경계를 보존하면서 이미지 구조, Compose 배치, 프로세스 간 계약, 장애 처리와 검증 계획을
구체화해야 한다.

| 항목 | 기준 |
| --- | --- |
| 작성 기준일 | 2026-08-14 |
| 기준 브랜치 | `feat/ai-speaker-debug` |
| 기준 커밋 | `2415dba` (`작업 영역 카메라 역할 추가`) |
| 현재 호스트 | Ubuntu 26.04, `x86_64` |
| 목표 호스트 | 현재 x86 서버와 향후 64-bit Raspberry Pi 계열 |

## 사용자와 합의된 방향

다음 내용은 상세 설계에서 임의로 되돌리지 않는다.

1. 메인 서비스와 카메라 publisher를 별도 저장소로 나누지 않는다.
2. 동일한 `smart-desk-fin` 저장소에서 역할별 이미지 또는 이미지 target을 만든다.
3. 각 장비는 동일한 소스 버전을 배포하되 필요한 실행 진입점과 설정만 활성화한다.
4. 메인 서비스, 영상 처리 worker와 Raspberry Pi 카메라 publisher를 모두 Docker로
   배포한다.
5. 초기에는 `fin-main`과 `fin-vision`을 같은 호스트에서 동시에 실행한다.
6. 메인 호스트 성능이 부족하거나 메인 서비스를 Raspberry Pi로 옮기면 `fin-vision`만
   같은 LAN의 다른 장비로 이동할 수 있어야 한다.
7. 영상 worker 이동은 소스 수정이나 이미지 재빌드가 아니라 endpoint와 배포 설정 변경으로
   가능해야 한다.
8. 영상 프레임은 MediaMTX RTSP로 전달하고, 영상 분석 결과는 작은 메시지 계약으로 메인에
   전달한다. 원본 프레임을 MQTT payload로 전달하지 않는다.
9. 물리 책상 제어와 안전 상태의 최종 소유자는 `fin-main`이다. Vision 단절이나 stale
   결과에서는 자동 제어를 fail-closed한다.

## 목표 배치

### 초기: 동일 호스트

```text
카메라 publisher ──RTSP──► MediaMTX ──RTSP──► fin-vision
                                                 │
                                                 │ MQTT observation/status
                                                 ▼
Browser ──HTTP──► fin-main ◄─────────────────── MQTT broker
                    │
                    ├─ Arduino serial
                    ├─ ESP32 relay/MQTT
                    ├─ WLED
                    └─ local audio (활성화 시)
```

동일 Compose project 안에서는 서비스 DNS 이름을 사용할 수 있다. 예를 들어
`rtsp://mediamtx:8554/workspace-cam`, `mqtt:1883`처럼 연결한다.

### 향후: Vision 원격 배치

```text
메인 Raspberry Pi                         Vision 처리 장비
┌───────────────────────┐                ┌──────────────────────┐
│ fin-main              │                │ fin-vision           │
│ MQTT broker           │◄──── LAN ─────►│ RTSP reader + models │
│ MediaMTX              │                └──────────────────────┘
└───────────▲───────────┘
            │ RTSP
      camera publishers
```

Docker bridge network는 호스트 하나의 범위다. 여러 호스트에서 같은 Compose 서비스명을
쓸 수 있다고 가정하지 않는다. 원격 배치에서는 LAN DNS 이름 또는 명시적인 고정 주소를
사용하고 필요한 포트만 노출한다. Docker Swarm이나 Kubernetes 도입은 현재 요구사항이
아니며, 단순한 호스트별 Compose 배포를 우선 검토한다.

## 권장 실행 단위

같은 저장소에서 다음 세 애플리케이션 이미지를 만드는 방향을 상세화한다.

| 이미지/실행 역할 | 책임 | 포함하지 않을 책임 |
| --- | --- | --- |
| `fin-main` | FastAPI, Dashboard, profile/SQLite, MQTT 소비, 책상·WLED·Voice 제어 | 모델 추론, 물리 카메라 직접 open |
| `fin-vision` | MediaMTX RTSP 수신, frame 전처리, 얼굴·재실·자세·workspace 분석, 관측 발행 | 책상 relay 명령, main SQLite 직접 접근 |
| `fin-camera-publisher` | V4L2 장치 하나를 FFmpeg로 열어 지정한 MediaMTX path에 publish | FastAPI, Dashboard, 모델 추론 |

MediaMTX와 MQTT broker는 애플리케이션 이미지에 합치지 않는다. 운영 Compose에서 별도
서비스로 관리할지 기존 외부 설치를 계속 사용할지는 상세 설계에서 배포·복구 책임까지
포함해 확정한다. Docker 전환 목적상 Compose 관리가 기본 후보지만, 기존 데이터와 다른
장치의 MQTT 사용 여부를 먼저 확인해야 한다.

카메라 장애 격리를 위해 publisher는 물리 카메라 하나당 컨테이너 하나를 권장한다.
현재 publisher CLI가 여러 카메라를 한 프로세스에서 실행할 수 있더라도 Compose에서는
역할별 컨테이너와 `--camera <role>`을 쓰는 안을 우선한다.

## 현재 코드 기준선

### 메인 애플리케이션

- `src/smart_desk/main.py`가 Uvicorn 애플리케이션을 노출한다.
- `src/smart_desk/bootstrap.py`가 MQTT, serial, desk, media, WLED와 Voice 자원을
  `AppContainer`에 조립한다.
- 하드웨어 singleton 때문에 Uvicorn worker는 반드시 1개다.
- `GET /health/live`와 `GET /health/ready`가 구현돼 있어 컨테이너 healthcheck 후보로
  사용할 수 있다.
- 운영 환경에서는 `frontend/dist`가 없으면 FastAPI 시작이 실패한다. `fin-main` 이미지는
  Node builder stage에서 React production build를 만든 뒤 Python runtime에 복사하는 안을
  검토해야 한다.
- SQLite 기본 경로는 `data/smart_desk.db`다. 컨테이너에서는 명시적인 persistent volume
  경로로 바꿔야 한다.
- Voice는 optional dependency이며 활성화하면 ALSA/PortAudio 장치, wakeword model,
  효과음과 OpenAI secret이 필요하다.
- 위 optional 동작은 현재 코드 기준선이다. task 02 완료 후 운영 `fin-main`은 Voice/Agents SDK
  dependency를 포함하고 Voice lifecycle을 항상 조립하며, 오디오 장치 단절만 기능별
  degraded 상태로 처리한다.
- Mem0 OSS는 별도 REST service가 아니라 `fin-main`에 Python library로 포함한다. 현재
  `data/mem0`, Docker 전환 후 `/app/data/mem0`를 persistent volume에 둔다.

### 카메라 송출

- `src/smart_desk/media_publish.py`는 FastAPI 없이 publisher만 실행하는 진입점이다.
- 설치 script 이름은 `smart-desk-media-publish`다.
- `--camera user`, `--camera workspace`, `--camera posture`를 지원한다.
- `CameraPublisher`는 FFmpeg를 `Popen(shell=False)`으로 실행하며 현재 encoder는
  `libx264`, preset은 `ultrafast`, RTSP transport는 TCP다.
- publisher 시작 시 설정된 device가 없거나 FFmpeg가 즉시 종료되면 컨테이너 프로세스도
  실패해야 한다. Compose의 `restart: unless-stopped`와 조합할 수 있다.
- Raspberry Pi에서 고해상도 `libx264` 소프트웨어 인코딩은 병목이 될 수 있다. encoder를
  설정화하거나 Raspberry Pi 하드웨어 encoder를 사용하는 설계는 실측 후 결정한다.

### 카메라 역할과 현재 장치

| 역할 | 현재 장치/배치 | 확인된 capture | RTSP path |
| --- | --- | --- | --- |
| `user` | 현재 호스트 Alcorlink USB 2.0 Camera | MJPEG 1920x1080, 15fps 검증 | `user-cam` |
| `workspace` | 현재 호스트 ABKO APC930 | MJPEG 2592x1944, 15fps 검증 | `workspace-cam` |
| `posture` | 현재 호스트에는 장치 없음. 별도 Raspberry Pi가 publish할 예정 | 실제 장치 연결 후 확정 | `posture-cam` |

ABKO 제품명에는 QHD가 포함되지만 현재 장치가 V4L2에서 광고한 최대 크기는
`2592x1944`이고 `2560x1440`은 광고하지 않았다. Docker 설정에서도 실제 검증값을
근거로 사용한다.

모든 카메라의 publish/receive 기본값은 현재 `false`다. posture의 기본 device
`/dev/posture-cam`은 컨테이너 안의 고정 경로 후보일 뿐, 현재 호스트에 실제 장치가 있다는
뜻이 아니다. 상세 설계에서는 device를 publish 활성화 시에만 필수로 검증하도록 설정 모델을
다듬을지 결정한다.

### 영상 수신과 Vision

- `RtspFrameSource`는 OpenCV/FFmpeg backend로 RTSP를 읽는 전용 thread 하나를 만들고
  queue 없이 최신 `(frame, time.monotonic())`만 보관한다.
- user·workspace·posture source 필드는 `AppContainer`에 있지만 분석 모델과
  `VisionStateService`는 아직 구현되지 않았다.
- 현재 `RtspFrameSource`는 `fin-main` bootstrap에서 생성된다. 목표 구조에서는 영상
  수신과 추론이 `fin-vision` 프로세스에 있어야 한다.
- Main과 Vision이 같은 프레임을 각각 읽는 중복 구조를 기본안으로 만들지 않는다. 브라우저
  preview처럼 별도 소비자가 필요한 경우만 MediaMTX에서 독립 stream을 읽는다.
- 기존 `docs/tasks/04-vision-observation.md`는 Vision을 단일 FastAPI 프로세스 내부
  singleton으로 전제한다. 상세 설계에서 네트워크 경계를 반영해 갱신해야 한다.

### MQTT와 외부 서비스

- 현재 MQTT client는 EMQX/MQTT 3.1.1, exact-topic handler와 QoS 0~2를 지원한다.
- 현재 설정에는 MQTT username/password/TLS가 없다.
- 기존 Vision 상수 `/smartdesk/vision`, `/smartdesk/vision/status`,
  `/smartdesk/vision/command`가 있으나 아직 구현 계약으로 확정된 것은 아니다.
- 2026-08-14 확인 시 호스트의 `mediamtx`와 `emqx` systemd unit은 inactive였고
  `mqtt-audit-logger`는 active였다. 설계·구현 전에 실제 broker와 MediaMTX의 설치 방식,
  데이터/설정 경로, 포트 점유를 다시 조사한다.
- Docker client와 Compose plugin은 설치돼 있지만 현재 사용자에게 Docker daemon socket
  접근 권한이 없다. 구현자는 권한 부여 방식과 운영 사용자를 확인해야 한다.

## Main–Vision 네트워크 계약

Vision을 나중에 다른 호스트로 옮기려면 현재부터 Python 객체 호출이나 공유 메모리 대신
명시적인 네트워크 계약을 사용해야 한다.

### 영상 입력

- publisher → MediaMTX → `fin-vision`은 RTSP를 사용한다.
- RTSP URL은 환경변수로 주입하며 코드에 `localhost` 또는 Compose 서비스명을 고정하지
  않는다.
- 같은 호스트에서는 내부 DNS를, 원격 배치에서는 LAN DNS/주소를 사용한다.
- MediaMTX는 가능한 한 relay만 담당하고, encoding은 publisher, decoding과 추론은
  Vision worker가 담당한다.

### 관측 결과

연속 관측에는 기존 MQTT 인프라를 재사용하는 안을 기본으로 검토한다. 최종 topic과 schema는
`docs/tasks/01-workflow-contracts.md` 및 Vision workflow와 함께 확정한다.

후보 topic 구조:

```text
/smartdesk/vision/user/observation
/smartdesk/vision/workspace/observation
/smartdesk/vision/posture/observation
/smartdesk/vision/status
```

최소 payload 후보:

```json
{
  "schema_version": 1,
  "camera": "posture",
  "source_instance": "vision-worker-01",
  "sequence": 12345,
  "captured_at": "2026-08-14T16:30:00.123Z",
  "processed_at": "2026-08-14T16:30:00.215Z",
  "model_version": "posture-v1",
  "result": {}
}
```

상세 설계에서 다음을 반드시 확정한다.

- topic 이름과 camera별 분리 여부
- JSON schema/version 호환 정책
- QoS와 retained 사용 여부
- worker heartbeat 주기, MQTT Last Will과 offline 표현
- frame·관측 만료 시간과 Main의 stale 판정
- 재연결 후 sequence 중복/역행 처리
- 여러 worker가 같은 camera 결과를 동시에 발행할 때 ownership 또는 generation 처리
- model load 실패, 부분 camera 단절과 전체 worker 단절의 구분
- clock skew 허용치와 NTP 전제

`time.monotonic()` 값은 호스트 사이에 전달하거나 비교할 수 없다. Vision은 UTC capture/
process 시각과 sequence를 발행하고, Main은 메시지 수신 순간의 local monotonic 시각도 함께
보관해 freshness를 판단하는 안을 우선 검토한다.

### Profile과 얼굴 임베딩

Main SQLite가 profile의 단일 소유자다. 원격 Vision 컨테이너가 SQLite 파일이나 Docker
volume을 직접 공유해서는 안 된다. 얼굴 식별에 profile/embedding이 필요해지면 다음 중
하나를 명시적으로 설계한다.

1. Vision이 Main의 인증된 내부 API에서 versioned profile snapshot을 가져와 memory에
   cache한다.
2. Main이 profile 변경 event를 MQTT로 발행하고 Vision이 시작 시 snapshot을 별도 API로
   동기화한다.

초기 단순안은 Main API pull + revision 확인이다. 원본 얼굴 이미지의 저장·전송 여부,
개인정보 범위와 삭제 동기화는 얼굴 task에서 별도로 확정한다.

## 역할별 설정 기준

현재 `Settings`는 Main 단일 프로세스 중심이고 Vision worker 전용 설정은 아직 없다.
상세 설계에서는 공통 값을 무조건 하나의 거대한 설정 객체에 넣기보다 각 진입점이 필요한
section만 검증할 수 있게 해야 한다. 특히 Main과 Vision이 MQTT에 동시에 연결하므로 서로
다른 `client_id`가 필요하다.

### 동일 호스트 Compose 후보

| 실행 역할 | 핵심 설정 후보 |
| --- | --- |
| `fin-main` | `MQTT__HOST=mqtt`, SQLite `/app/data/smart_desk.db`, Mem0 `/app/data/mem0`, 세 camera publish/receive 모두 false |
| `fin-vision` | `MQTT__HOST=mqtt`, 고유 client ID, RTSP host `mediamtx`, camera별 receive/분석 enable |
| user publisher | user publish만 true, `DEVICE=/dev/user-cam`, `PUBLISH_URL=rtsp://mediamtx:8554/user-cam` |
| workspace publisher | workspace publish만 true, `DEVICE=/dev/workspace-cam`, `PUBLISH_URL=rtsp://mediamtx:8554/workspace-cam` |
| posture publisher | 해당 호스트에 장치가 없으므로 배포하지 않음 |

### 원격 posture publisher 후보

```text
SMART_DESK_MEDIA__POSTURE__PUBLISH_ENABLED=true
SMART_DESK_MEDIA__POSTURE__RECEIVE_ENABLED=false
SMART_DESK_MEDIA__POSTURE__DEVICE=/dev/posture-cam
SMART_DESK_MEDIA__POSTURE__PUBLISH_URL=rtsp://media.smartdesk.lan:8554/posture-cam
```

### 원격 Vision 후보

아래 이름은 아직 코드에 없으며 상세 설계에서 naming과 Settings model을 확정해야 한다.

```text
SMART_DESK_VISION__INSTANCE_ID=vision-worker-01
SMART_DESK_VISION__MQTT_HOST=mqtt.smartdesk.lan
SMART_DESK_VISION__USER__RTSP_URL=rtsp://media.smartdesk.lan:8554/user-cam
SMART_DESK_VISION__WORKSPACE__RTSP_URL=rtsp://media.smartdesk.lan:8554/workspace-cam
SMART_DESK_VISION__POSTURE__RTSP_URL=rtsp://media.smartdesk.lan:8554/posture-cam
```

배포용 env에서는 기존 `.env`의 `127.0.0.1` 기본값을 그대로 사용하지 않는다. 설정 example은
실제 secret 없이 역할별로 제공하고, 같은 key를 여러 override 파일에서 상충되게 정의하지
않도록 최종 merge 결과를 검증하는 명령도 운영 문서에 포함한다.

## Docker 이미지 상세 설계 요구사항

### 공통

- multi-stage Dockerfile 또는 역할별 Dockerfile 중 유지보수성과 layer 공유를 비교한다.
- 동일 Git SHA에서 `fin-main`, `fin-vision`, `fin-camera-publisher` tag를 재현 가능하게
  만든다.
- 현재 x86 호스트용 `linux/amd64`와 Raspberry Pi용 `linux/arm64`를 지원한다.
- 32-bit Raspberry Pi OS는 기본 지원 대상에서 제외하고 필요 시 별도 결정한다.
- runtime은 root가 아닌 고정 UID/GID를 기본으로 한다. 장치 group 권한은 Compose에서
  명시한다.
- 로그는 파일이 아니라 stdout/stderr로 보낸다.
- `.env`, OpenAI key, MQTT/MediaMTX credential과 로컬 SQLite를 image layer에 복사하지
  않는다.
- `.dockerignore`에는 `.git`, `.venv`, `.env`, `data`, frontend cache/build output,
  test cache와 firmware build artifact를 포함한다.
- PID 1 signal 전달과 graceful shutdown을 실제 확인한다.

### `fin-main`

- frontend Node build stage와 Python runtime stage를 분리한다.
- Uvicorn worker 1개를 명시하고 기존 `/health/live`, `/health/ready`를 healthcheck로
  사용한다.
- SQLite volume과 serial 장치를 연결한다. production은 audio 장치도 연결하고, 장치 없는
  개발·CI profile은 Voice 객체를 생략하지 않고 fake 또는 degraded 상태를 사용한다.
- `mem0ai`는 Main image의 Voice/AI dependency에 포함하고 `/app/data/mem0`를 영속 volume으로
  연결한다. 단일 Main worker에서는 별도 Mem0 API·Dashboard·Postgres container를 만들지 않는다.
- Main 컨테이너에서는 카메라 publish와 RTSP receive를 기본적으로 끈다. 카메라는 전용
  publisher, 영상 처리는 `fin-vision`이 담당하는 배치를 기본으로 한다.
- Voice가 없는 경량 image/target은 개발·CI 전용으로만 검토한다. production `fin-main`에서
  Voice lifecycle 등록을 생략하는 선택지로 사용하지 않는다.

### `fin-camera-publisher`

- 최소 Python runtime, Pydantic settings, FIN publisher 코드와 FFmpeg만 포함한다.
- FastAPI, frontend, OpenCV, Voice/model dependency를 가능한 한 설치하지 않는다.
- 한 컨테이너가 지정한 camera 하나만 실행하도록 command를 명시한다.
- FFmpeg와 자식 process 종료가 Docker SIGTERM에서 제한 시간 안에 완료되는지 확인한다.
- encoder 설정화 전에는 `libx264` CPU 요구량을 각 Raspberry Pi에서 측정한다.

### `fin-vision`

- 아직 진입점과 서비스가 없으므로 상세 설계 산출물에 process lifecycle을 포함한다.
- OpenCV/FFmpeg, model runtime과 architecture별 native dependency를 분리해 관리한다.
- 시작 시 모델을 한 번 load하고, 최신 frame만 처리하며 무제한 frame queue를 두지 않는다.
- camera별 분석 FPS, resize/crop, ROI, frame skip과 model enable 설정을 둔다.
- process liveness와 “모델·MQTT·필수 stream 준비” readiness를 구분한다.
- 컨테이너 healthcheck용 작은 HTTP endpoint가 필요한지, process check와 MQTT heartbeat로
  충분한지 비교한다. Main의 FastAPI 앱 전체를 Vision image에 실행하지 않는다.
- CPU/GPU/NPU 가속 backend는 adapter/config 경계로 두되 실제 대상 하드웨어가 정해지기
  전에 범용 plugin framework를 만들지 않는다.

## Compose와 호스트 설정 요구사항

권장 파일 구성 후보는 다음과 같다. 이름은 상세 설계에서 확정한다.

```text
deploy/
├── compose.yml                  공통 서비스와 network/volume
├── compose.main-host.yml        Main 장치·포트 override
├── compose.vision-host.yml      원격 Vision 배치 override
├── compose.camera-host.yml      Raspberry Pi publisher 배치 예시
├── env/                         secret을 제외한 역할별 example
└── mediamtx/                    저장소에서 관리하기로 한 경우 설정 template
```

Compose 설계에는 다음이 포함돼야 한다.

- 서비스별 command, restart policy, healthcheck와 stop grace period
- Main HTTP, MediaMTX RTSP/WebRTC/HLS, MQTT 중 외부 노출이 필요한 포트
- 내부 network와 LAN 노출 경계
- SQLite와 Mem0 named volume 또는 bind mount 및 각각의 backup 경로
- model cache/weight의 image 포함 여부 또는 read-only mount
- hardware 없는 개발 profile
- user/workspace 로컬 publisher profile
- posture Raspberry Pi publisher profile
- 동일 호스트 Vision과 원격 Vision의 환경변수 차이
- 서비스 시작 순서에 의존하지 않는 application-level retry

`depends_on`은 원격 호스트 장애나 런타임 단절을 해결하지 않는다. publisher와 Vision은
MediaMTX/MQTT가 늦게 시작하거나 재시작해도 복구해야 하며, Main은 Vision stale 상태에서
자동화를 차단해야 한다.

## 물리 장치 전달

`privileged: true`를 기본안으로 사용하지 않는다. 필요한 장치만 명시적으로 전달한다.

| 기능 | 호스트 장치 예 | 컨테이너 고정 경로 후보 |
| --- | --- | --- |
| user camera | 실제 `/dev/videoN` | `/dev/user-cam` |
| workspace camera | 실제 `/dev/videoN` | `/dev/workspace-cam` |
| posture camera | Raspberry Pi 실제 `/dev/videoN` | `/dev/posture-cam` |
| Arduino height | `/dev/serial/by-id/...` | `/dev/desk-height` |
| audio | `/dev/snd` | `/dev/snd` |

호스트 `/dev/v4l/by-id` symlink만 bind해도 device cgroup 권한이 자동 해결되는 것은 아니다.
Compose `devices`로 실제 장치 node를 고정 컨테이너 경로에 mapping하는 안을 검증한다. USB
재연결로 실제 node가 바뀌면 container restart나 udev 연계가 필요할 수 있다. 광범위한
`c 81:*` 허용이나 privileged mode는 편의성보다 접근 범위를 먼저 평가한다.

오디오 컨테이너화는 카메라보다 복잡하다. ALSA 직접 mapping, host PulseAudio/PipeWire
socket 사용 여부와 실행 UID의 audio group을 실제 장치에서 검증한다. 첫 Compose 검증은
장치 없는 degraded/fake Voice로 진행할 수 있다. Voice를 활성화한 production profile에서는
audio mapping과 Voice lifecycle을 반드시 검증하고, Voice 비활성 profile은 정상
`DISABLED`로 구분한다.

## 네트워크와 보안

- 컨테이너 내부의 `127.0.0.1`은 다른 서비스나 호스트를 가리키지 않는다. 기존 `.env`의
  MQTT/RTSP localhost 기본값을 배포용 env에서 반드시 덮어쓴다.
- 원격 Vision 또는 publisher 배치 시 MediaMTX와 MQTT를 LAN에 노출해야 한다.
- 현재 MQTT client에는 인증/TLS 설정이 없으므로 username/password/TLS 또는 최소한
  방화벽/IP allowlist 적용 범위를 상세 설계한다.
- MediaMTX publisher와 reader 권한을 role/path별로 분리하는 안을 검토한다.
- Dashboard/API의 LAN 노출과 인증 요구도 함께 확인한다.
- secret은 Git의 Compose 파일이나 `.env.example`에 실제 값으로 넣지 않는다. Docker
  secret, host env file 또는 운영 secret manager 중 프로젝트 규모에 맞는 방식을 고른다.
- 불필요한 `network_mode: host`를 피한다. mDNS/WLED/audio 등 실제 요구로 필요한 경우만
  근거와 노출 범위를 문서화한다.

## 성능 설계와 측정

“다른 Raspberry Pi로 옮길 수 있음”과 “해당 Raspberry Pi가 실제로 충분히 빠름”은 별개다.
다음 값을 현재 x86과 목표 Raspberry Pi에서 측정하는 계획을 포함한다.

- 카메라별 publish CPU, bitrate와 frame drop
- H.264 decode CPU와 end-to-end frame age
- 전처리/모델별 추론 시간과 처리 FPS
- 세 stream 동시 처리 시 memory와 온도/throttling
- Vision 부하 중 Main의 HTTP/STOP latency
- MQTT observation 지연, 중복과 단절 후 복구 시간

고해상도 stream은 preview/보관 해상도와 분석 해상도를 분리할 수 있다. 분석 worker에서
resize와 frame skip을 우선 적용하고, 그래도 네트워크나 decode 비용이 크면 MediaMTX의
별도 저해상도 path 또는 publisher의 hardware encoding을 검토한다. 측정 전에 복잡한
다중 quality pipeline을 먼저 구현하지 않는다.

## 장애와 안전 요구사항

상세 설계에는 최소한 다음 failure matrix가 있어야 한다.

| 장애 | 기대 동작 |
| --- | --- |
| publisher 종료/카메라 분리 | 해당 stream만 offline, 다른 역할과 Main은 계속 실행 |
| MediaMTX 종료 | publisher/vision 재연결, Main은 Vision stale로 자동화 차단 |
| Vision 종료/모델 실패 | Main은 마지막 결과를 무기한 사용하지 않고 `UNKNOWN` 처리 |
| MQTT 종료 | Main의 기존 readiness/제어 안전 정책을 검토하고 Vision 결과는 stale 처리 |
| Main 재시작 | profile/설정은 volume에서 복구, Vision 결과는 새 session 기준으로 수신 |
| Vision worker 중복 실행 | instance/generation 정책에 따라 한 source만 유효 처리 |
| 호스트 clock 불일치 | 허용 범위 초과 관측을 사용하지 않고 clock 문제 노출 |
| container SIGTERM | relay 안전 정지, FFmpeg/reader/model task 유한 시간 내 종료 |

Vision 상태가 없더라도 수동 STOP은 항상 가능해야 한다. Vision 결과가 stale·불일치·다중
사용자·worker offline이면 자동 이동 admission을 허용하지 않는 기존 안전 원칙을 유지한다.

## 상세 설계 작업자가 만들어야 할 산출물

구현 전에 다음 결과를 문서로 확정한다.

1. 역할별 process/container 책임과 의존 관계
2. 현재 동일 호스트와 향후 다중 호스트의 deployment diagram
3. Dockerfile/target, build context와 multi-architecture 전략
4. Compose base/override/profile 구조와 파일 배치
5. 역할별 환경변수 matrix와 example 파일
6. 장치·volume·port·network·secret mapping 표
7. Main–Vision MQTT topic, JSON schema와 freshness/ownership 계약
8. profile/embedding 동기화 방식
9. health/readiness/restart/graceful shutdown 정책
10. failure matrix와 desk fail-closed 동작
11. x86/arm64 build 및 성능 검증 계획
12. 기존 systemd 서비스에서 Compose로 전환하고 되돌리는 migration/rollback 절차

설계 문서에는 “추후 결정”만 남기지 말고, 아직 하드웨어 정보가 없어 확정할 수 없는 항목은
필요한 측정, 담당 배포 환경과 결정 시점을 함께 적는다.

## 권장 구현 순서

각 단계는 독립적으로 검토·rollback 가능한 커밋으로 나눈다.

### 1. 배포 계약과 Main image

- 과거 단일 프로세스/비-Docker 문서 전제를 새 결정으로 갱신한다.
- `.dockerignore`, Main multi-stage build와 non-root runtime을 추가한다.
- frontend 정적 build, SQLite volume, healthcheck와 graceful shutdown을 검증한다.
- hardware를 모두 끈 개발 Compose로 기존 전체 테스트와 HTTP health를 통과시킨다.

### 2. Publisher image

- publisher 최소 dependency target/image를 만든다.
- 카메라 하나당 컨테이너 하나의 Compose profile을 추가한다.
- x86 user/workspace 실제 장치와 Raspberry Pi posture 예시를 분리한다.
- camera 분리, MediaMTX 지연 시작, SIGTERM과 restart를 검증한다.

### 3. Infra Compose

- MediaMTX와 MQTT를 Compose가 소유할지 확정하고 설정/volume/인증을 추가한다.
- 기존 systemd unit과 포트 충돌을 탐지하는 migration 절차를 작성한다.
- browser preview에 필요한 MediaMTX 포트는 실제 protocol 결정 뒤 노출한다.

### 4. Vision process 경계

- `fin-vision` 진입점과 전용 설정/lifecycle을 만든다.
- `RtspFrameSource`와 전처리/추론을 Main bootstrap 밖으로 옮긴다.
- 모델 구현 전 fake observation publisher/consumer로 MQTT 계약, stale와 heartbeat를 먼저
  검증한다.
- Main에는 최신 Vision snapshot 소비와 fail-closed 상태만 둔다.

### 5. 원격 배치 검증

- 동일 호스트 Compose에서 end-to-end를 검증한다.
- `fin-vision`만 다른 LAN 호스트로 옮기고 endpoint 변경만으로 동일 테스트를 반복한다.
- 네트워크 단절, 재연결, clock skew와 중복 worker를 시험한다.
- 목표 Raspberry Pi에서 실제 성능을 기록하고 encoder/model 설정을 확정한다.

## 완료 기준

다음 조건을 모두 충족해야 Docker/분산 Vision 기반 작업을 완료로 볼 수 있다.

- 동일 Git SHA로 `linux/amd64`와 `linux/arm64` 역할별 이미지를 빌드할 수 있다.
- secret과 runtime data가 image나 Git에 포함되지 않는다.
- hardware 없는 환경에서 Main과 infra가 healthcheck를 통과한다.
- SQLite는 container recreate 후 유지되고 backup/restore 절차가 검증된다.
- user/workspace/posture publisher를 역할별 독립 컨테이너로 실행할 수 있다.
- Main과 Vision을 같은 호스트에서 실행할 수 있다.
- Vision만 다른 LAN 호스트로 이동해도 image 변경 없이 RTSP 입력과 MQTT 결과 전달이
  동작한다.
- Vision/MediaMTX/MQTT/camera 단절에서 stale 결과가 자동 제어에 사용되지 않는다.
- Main의 STOP과 주요 HTTP 응답이 Vision 추론 부하에 의해 허용 범위를 넘지 않는다.
- 컨테이너 종료 시 desk 제어와 자식 process가 안전하게 정리된다.
- 실제 Raspberry Pi에서 CPU, memory, 온도, frame age와 처리 FPS 측정 결과가 문서화된다.

## 상세 설계 전에 확인할 질문

다음 정보는 저장소만으로 확정할 수 없다. 권장 기본안을 함께 제시하고 사용자에게 확인한다.

| 질문 | 권장 기본안 |
| --- | --- |
| Main/vision/publisher Raspberry Pi 모델과 RAM | 모두 64-bit OS, 실제 모델별 성능 측정 |
| image registry 위치 | private GHCR 또는 사용 중인 내부 registry |
| MQTT/MediaMTX를 Compose가 소유할지 | 신규 배포부터 Compose 소유, 기존 설정 migration |
| LAN DNS/고정 주소 방식 | `media.smartdesk.lan`, `mqtt.smartdesk.lan` 같은 고정 이름 |
| MQTT/MediaMTX 인증 | 최소 계정 분리와 LAN firewall 적용 |
| Main 호스트에 남을 serial/audio 장치 | serial과 audio 모두 Main에 연결, audio mapping은 별도 실측 |
| Vision 모델과 가속 backend | 모델 선정 후 CPU 기준선 측정, 필요 시 하드웨어 가속 추가 |
| browser preview protocol | MediaMTX WebRTC/HLS 실측 뒤 하나를 기본으로 확정 |

## 반드시 갱신할 기존 문서

현재 문서에는 이번 결정과 충돌하는 과거 단일 프로세스/비-Docker 전제가 있다. 상세 설계와
같은 변경에서 최소한 다음 문서를 갱신한다.

- `README.md`: Docker build/deploy와 역할별 실행 진입점
- `docs/README.md`: 단일 프로세스 설계 결정과 문서 안내
- `docs/PROJECT_STRUCTURE.md`: “Compose를 만들지 않는다”는 문구와 새 `deploy/` 구조
- `docs/architecture/system-design.md`: Vision process/network 경계와 Docker 제외 범위
- `docs/architecture/component-design.md`: Main/Vision lifecycle과 MQTT adapter
- `docs/architecture/runtime-and-concurrency.md`: 프로세스별 singleton 범위
- `docs/guides/project-principles.md`: 단일 프로세스와 Docker 비사용 기본 선택
- `docs/implementation/roadmap.md`: containerization과 Vision 분리 단계
- `docs/tasks/04-vision-observation.md`: Main 내부 Vision service 전제를 worker 계약으로 변경
- `docs/tasks/09-system-validation.md`: container/다중 호스트 장애·복구 검증

과거 문구를 조용히 남겨 두지 않는다. 이번 변경은 요구사항 변화에 따른 의도적인 설계 전환이며,
Main 프로세스 내부에서는 여전히 worker 1개와 명시적인 lifecycle 원칙을 유지한다.

## 착수 시 확인 명령

```bash
cd /srv/smart-desk-fin
git status --short --branch
git log -5 --oneline
rg --files -g 'Dockerfile*' -g 'compose*.yml' -g '.dockerignore'
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q src tests
cd frontend && npm run build
```

Docker daemon 권한을 확보한 후에는 최소 hello-world가 아니라 실제 target의 build context,
UID/GID, network와 volume 접근부터 검증한다. 기존 systemd MediaMTX/EMQX를 중지하거나
변경하기 전에는 unit 상태, 설정, 데이터 경로와 rollback 방법을 먼저 기록한다.
