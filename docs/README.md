# SMART DESK FIN 설계 문서

`smart-desk-fin`의 리엔지니어링 구조와 구현 순서를 안내한다. 현재
`/srv/smart-desk`의 동작·MQTT 계약·하드웨어 안전 기준을 보존하면서, 단일
FastAPI 프로세스와 `asyncio` 기반 구조로 단순화한다.

## 현재 구현 단계

설정, `AppContainer`, 공유 자원 수명주기, `TaskManager`, 구조화 로그와 FastAPI
health API에 더해 EMQX 연결·발행·구독·재연결을 담당하는 `MqttClient`까지
구현되어 있다. Desk와 Vision 컴포넌트는 아직 설계 초안이다. React + TypeScript
+ Vite 대시보드 골조와 FastAPI 정적 제공은 구현되어 있다.

| 구현 영역 | 현재 코드 |
| --- | --- |
| 실행 진입점과 FastAPI 생성 | `src/smart_desk/main.py`, `application.py` |
| 객체 조립 | `src/smart_desk/bootstrap.py` |
| 설정 | `src/smart_desk/config/settings.py` |
| singleton container | `src/smart_desk/core/container.py` |
| 시작·종료 | `src/smart_desk/core/lifecycle.py` |
| async 작업 감독 | `src/smart_desk/core/task_manager.py` |
| MQTT 연결과 메시지 전달 | `src/smart_desk/modules/mqtt` |
| 상태 확인 | `src/smart_desk/api/routes/health.py` |
| React 개발 소스 | `frontend/src` |
| React 정적 제공 | `src/smart_desk/frontend.py` |

## 문서 안내

| 문서 | 읽는 시점 | 다루는 내용 |
| --- | --- | --- |
| [프로젝트 구조](PROJECT_STRUCTURE.md) | 파일을 찾거나 추가할 때 | 현재 폴더·파일 책임과 배치 기준 |
| [시스템 구조](architecture/system-design.md) | 전체 구조를 정할 때 | 프로세스 경계, 계층, 데이터 흐름 |
| [컴포넌트 설계](architecture/component-design.md) | 클래스를 만들 때 | 클래스 책임, 공개 API, 의존 방향 |
| [실행과 동시성](architecture/runtime-and-concurrency.md) | 앱 시작·비동기 루프를 구현할 때 | 컨테이너, singleton 접근, Task 수명주기 |
| [React 대시보드](architecture/frontend.md) | UI를 개발·배포할 때 | Vite 개발 서버, FastAPI 운영 제공 |
| [책상 제어와 안전](architecture/desk-safety.md) | 높이·릴레이 제어를 구현할 때 | 제어 상태, STOP 우선순위, 하드웨어 경계 |
| [계획 및 설계 가이드](guides/README.md) | 새 구조나 작업을 제안하기 전에 | 프로젝트 규모, 복잡도, 계획·검증·커밋 판단 기준 |
| [구현 순서](implementation/roadmap.md) | 개발 계획을 세울 때 | 2~3개월 단계와 완료 조건 |
| [작업 목록](tasks/README.md) | 실제 구현을 시작할 때 | 번호순 작업, 선행 조건, 검증과 완료 기준 |

## 설계 결정

- 하나의 Python 프로세스에서 FastAPI, Vision, 책상 제어, MQTT 작업을 함께 실행한다.
- Uvicorn worker는 하나만 사용하고, 단기 프로젝트 범위에서 별도 process lock이나
  supervisor는 추가하지 않는다.
- 프로세스 안의 공유 자원은 `AppContainer`가 한 번 생성하고 `get_*()` 함수로
  접근한다. 이는 프로세스 내부에만 적용되는 singleton이다.
- API와 이벤트 handler는 함수 내부에서 `get_*()`를 직접 호출할 수 있다.
  핵심 서비스 클래스는 필요한 객체를 생성자로 전달받는다.
- 클래스는 장치 I/O, 최신 상태 보관, 정책 판단을 분리한다.
- Vision 추론처럼 이벤트 루프를 오래 점유하는 작업은 thread executor에서 실행한다.
- 물리 웹캠은 카메라별 FFmpeg publisher가 열어 MediaMTX에 RTSP로 발행한다.
  Python은 `RtspFrameSource`로 스트림만 읽으며 별도 업로더를 만들지 않는다.
- EMQX, MediaMTX와 FFmpeg publisher는 Python singleton이 아닌 외부 인프라다.
- `DeskController`만 릴레이 명령을 결정하고, ESP32의 독립 안전 제한은 유지한다.
- 현재 critical task 실패는 readiness를 내리는 데까지만 처리한다. 실제 ESP32
  STOP 보장은 Desk 제어 루프 구현 단계에서 추가한다.

## 보존 전제

아래 내용은 구현 전에 `/srv/smart-desk/docs` 및 펌웨어와 다시 대조한다.

- MQTT 토픽과 JSON 필드
- 세그먼트 측정 범위 73~128cm
- 확정된 책상 물리 최대 높이 118cm와 기본 운영 범위 75~118cm
- 릴레이 펄스 시간, watchdog, timeout, STOP 조건
- ESP32의 연결 끊김·상하한·펄스 만료 보호
- 인증 없이 사용하는 로컬 EMQX의 MQTT TCP 주소 `127.0.0.1:1883`

이 문서는 새 구조를 정의한다. 기존 동작 계약의 기준 문서는
`/srv/smart-desk/docs/MQTT_PROTOCOL.md`, `SAFETY.md`, `HTTP_API.md`다. 단, 기존
문서의 더 높은 운영 상한보다 이 프로젝트에서 확정한 물리 최대 118cm가 우선한다.
