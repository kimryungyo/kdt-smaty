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
     ┌────────────┼─────────────────────────┬────────────┐
     │            │                         │            │
     ▼            ▼                         ▼            ▼
Dashboard   Automation               MQTT client    WLED client
service     service                       │              │
   │            │                        ESP32          WLED
   │            ▼
   │      DeskController ◄── DeskHeightMonitor ◄── Arduino serial
   │            │
   │            ▼
   │        RelayClient
   │
   └──────► VisionStateService ◄── FaceRecognizer / PostureDetector
                                      ▲
                                      │
                             FramePreprocessor ◄── WebRtcFrameSource
```

각 상자는 같은 프로세스의 객체다. 객체 사이의 호출은 메모리 안에서 직접 이뤄진다. 운영
ESP32는 서버와 Wi-Fi/MQTT로 통신하고, Arduino 높이 리더만 별도 USB serial을 사용하며 WLED는
HTTP를 사용한다. MQTT→USB-serial bridge는 운영 구성에 없다.

영상 입력은 FastAPI lifespan이 카메라별 WHIP publisher와 WHEP reader를
시작해 다음 경로로 준비한다.

```text
USB webcam ─ WebRtcCameraPublisher ─ WHIP ─ host MediaMTX
                                              ├─ WebRTC ─ Browser
                                              └─ WHEP ─ WebRtcFrameSource ─ Vision
```

`WebRtcCameraPublisher`가 PyAV로 물리 카메라를 단독으로 열고 카메라별 WHIP
endpoint에 영상을 발행한다. `WebRtcFrameSource`는 `/dev/video*`를 직접
열거나 MediaMTX에 프레임을 업로드하지 않고 WHEP를 읽어 최신 프레임 하나만
보관한다. 호스트에서 이미 실행 중인 MediaMTX만 애플리케이션 밖의 선행
인프라이며 FastAPI는 이를 시작하거나 종료하지 않는다.

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
| 카메라 최신 프레임 | `WebRtcFrameSource` | `get_latest_frame()` |
| 전처리 프레임 | `FramePreprocessor` | `get_latest_frame()` |
| 얼굴·자세·재실 결과 | 각 detector와 `VisionStateService` | `get_snapshot()` |
| 프로필과 영속 설정 | `ProfileRepository` | 조회·저장 메서드 |
| 제어 방식·active 작업 모드 | `AutomationService` | 명령 메서드, `get_snapshot()` |

상태를 직접 수정하는 외부 코드는 허용하지 않는다. 다른 객체는 공개 명령이나
불변 snapshot을 통해서만 상태를 읽고 바꾼다.

## 의도적으로 포함하지 않는 것

- 프로세스 간 Python 객체 공유
- 브라우저의 MQTT 직접 연결
- 여러 publisher가 같은 물리 웹캠을 동시에 여는 구조
- Python `MediaMtxUploader` 또는 프레임 업로드 API
- MediaMTX를 위한 Docker·Compose 재구성
- publisher manager, factory, registry와 별도 process supervisor
- Dashboard나 Vision에서의 릴레이 직접 제어
- MQTT→USB-serial bridge와 ESP32 serial 운영 fallback
- 프레임을 무제한으로 쌓는 큐 기반 영상 파이프라인

영상은 실시간성을 우선하므로 최신 프레임을 덮어쓴다. 프레임 유실보다 오래된
추론 결과로 제어하는 것이 더 위험하다.
