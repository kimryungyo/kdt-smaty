# 시스템 구조

단일 프로세스 async 구조의 전체 책임과 데이터 흐름을 정의한다. 이 문서는
배포 단위를 늘리지 않고도 클래스 간 경계를 명확히 하기 위한 설계 초안이다.

## 실행 모델

하나의 FastAPI 프로세스가 HTTP 서버와 장기 실행 작업을 함께 소유한다.

```text
Browser ─ HTTP ─┐
                ▼
     React dashboard / FastAPI API
                │
                ▼
          FastAPI application
                │
     ┌────────────┼─────────────────────────┐
     │            │                         │
     ▼            ▼                         ▼
Dashboard   Automation               MQTT client
service     service                   │       │
   │            │                      ESP32   WLED
   │            ▼
   │      DeskController ◄── DeskHeightMonitor ◄── Arduino serial
   │            │
   │            ▼
   │        RelayClient
   │
   └──────► VisionStateService ◄── FaceRecognizer / PostureDetector
                                      ▲
                                      │
                             FramePreprocessor ◄── RtspFrameSource
```

각 상자는 같은 프로세스의 객체다. 객체 사이의 호출은 메모리 안에서 직접
이뤄지고, ESP32·Arduino·WLED 같은 외부 장치와의 경계에서만 MQTT, 시리얼,
HTTP를 사용한다.

영상 입력은 Python 프로세스 밖에서 다음 경로로 준비한다.

```text
USB webcam ─ FFmpeg publisher ─ RTSP ─ MediaMTX
                                         ├─ WebRTC/HLS ─ Browser
                                         └─ RTSP ─ RtspFrameSource ─ Vision
```

FFmpeg가 물리 카메라를 단독으로 열고 카메라별 RTSP 경로에 영상을 발행한다.
Python은 `/dev/video*`를 직접 열거나 MediaMTX에 프레임을 업로드하지 않고 RTSP를
읽는다. MediaMTX와 FFmpeg는 `AppContainer` singleton이 아니라 별도 인프라
프로세스이며, Python 애플리케이션이 재시작되어도 독립적으로 동작할 수 있다.

Uvicorn은 worker 하나로 실행한다. 하나의 worker 안에서 HTTP, MQTT, 높이 갱신과
제어 작업이 여러 async task로 함께 동작한다. 단기 프로젝트 범위에서는
마이크로서비스 분리, 프로세스 간 객체 공유, 별도 process lock을 구성하지 않는다.

개발 중 React는 Vite 개발 서버에서 실행하고 API 요청을 FastAPI로 proxy한다.
운영 중에는 Vite가 만든 정적 `frontend/dist`를 FastAPI가 같은 포트에서
제공한다. React는 Python 프로세스 안에서 실행되는 것이 아니라 브라우저에서
실행된다.

## 계층과 의존 방향

```text
api, mqtt handler
       ↓
application services (Dashboard, Automation)
       ↓
controllers / state services (Desk, Vision)
       ↓
adapters (Relay, Serial, Camera, Storage)
       ↓
physical devices and files
```

- API 라우트는 요청 검증과 서비스 호출만 담당한다.
- `AutomationService`는 Vision 상태와 프로필을 읽어 책상 목표를 판단한다.
- `DeskController`는 목표·수동 제어·정지 상태를 관리하고 릴레이 동작을 결정한다.
- 어댑터는 장치 프로토콜을 숨기지만 사용자 정책이나 목표 높이는 판단하지 않는다.

## 상태 소유권

| 상태 | 소유 클래스 | 다른 클래스의 사용 방식 |
| --- | --- | --- |
| 최신 센서 높이와 수신 시각 | `DeskHeightMonitor` | `get_snapshot()` |
| 목표, 이동 방향, 제어 상태 | `DeskController` | 명령 메서드, `get_snapshot()` |
| 카메라 최신 프레임 | `RtspFrameSource` | `get_latest_frame()` |
| 전처리 프레임 | `FramePreprocessor` | `get_latest_frame()` |
| 얼굴·자세·재실 결과 | 각 detector와 `VisionStateService` | `get_snapshot()` |
| 프로필과 영속 설정 | `ProfileRepository` | 조회·저장 메서드 |

상태를 직접 수정하는 외부 코드는 허용하지 않는다. 다른 객체는 공개 명령이나
불변 snapshot을 통해서만 상태를 읽고 바꾼다.

## 의도적으로 포함하지 않는 것

- 프로세스 간 Python 객체 공유
- 브라우저의 MQTT 직접 연결
- Python에서 물리 웹캠을 직접 여는 동시에 FFmpeg도 같은 장치를 여는 구조
- Python `MediaMtxUploader` 또는 프레임 업로드 API
- Dashboard나 Vision에서의 릴레이 직접 제어
- 프레임을 무제한으로 쌓는 큐 기반 영상 파이프라인

영상은 실시간성을 우선하므로 최신 프레임을 덮어쓴다. 프레임 유실보다 오래된
추론 결과로 제어하는 것이 더 위험하다.
