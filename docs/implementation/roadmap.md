# 구현 순서

2~3개월 안에 시연 가능한 결과를 만드는 순서다. 각 단계는 이전 단계의 안전
검증이 통과한 뒤 진행한다.

## 0단계: 기본 골조 — 완료

- FastAPI lifespan, `AppContainer`, Settings와 `TaskManager`를 구현했다.
- React + TypeScript + Vite 골조와 FastAPI 정적 제공을 구현했다.
- live/ready API와 기본 단위·통합 테스트를 구현했다.

완료 조건: worker 하나에서 앱이 시작·종료되고 React build와 health API가 동작한다.

## 1단계: MQTT 기반과 안전한 책상 제어 — 진행 중

- 인증 없는 EMQX에 연결하는 `MqttClient`와 토픽·메시지 계약을 구현했다.
- `SerialLineSource`, `DeskHeightMonitor`, `SegmentDecoder`와 `RelayClient`를
  구현했다.
- `DeskController`의 목표·HOLD·STOP 정책과 FIN ESP32 relay firmware를 구현했다.
- 기존 MQTT 토픽·JSON·높이 범위·ESP32 보호를 그대로 연결한다.
- 목표, HOLD, STOP, 센서 만료, 서버 종료 단위 테스트와 firmware native protocol
  test를 만들었다.

Python·fake 검증과 firmware clean build는 완료했다. relay 분리 board test와 실제
책상 검증 전에는 이 단계를 전체 완료로 표시하지 않는다.

완료 조건: 센서 없이 릴레이를 켜지 않으며, 모든 종료 경로에서 STOP 요청을 한다.

## 2단계: Dashboard와 프로필

- `ProfileRepository`, `DashboardService`, FastAPI 라우트를 구현했다.
- 상태 조회, 수동 제어, 목표 설정, 프로필 CRUD를 연결했다.
- `/api` 계약과 계약 테스트는 [Dashboard HTTP API](../api-dashboard.md)에 확정했다.
- 후속 profile 작업은 SQLite v3 `profile_modes`로 독서·공부 같은 작업 모드의 앉기·서기
  높이와 LED를 저장한다. 기존 단일 높이 custom preset은 추가하지 않는다.

완료 조건: 브라우저에서 실제 높이·Desk 상태를 보고 안전한 수동/목표 제어를 할 수 있다.

## 3단계: 영상 인프라와 Vision 파이프라인

- 기존 호스트 MediaMTX를 사용하고 FastAPI가 카메라별 WHIP publisher를 실행한다.
- 카메라별 `WebRtcFrameSource`가 WHEP 최신 프레임 하나를 제공하는 기반은 구현돼 있다.
- [Vision 관측 작업](../tasks/04-vision-observation.md)에서 `FramePreprocessor`와
  Vision 판정을 설계·구현한다.
- 자세, 얼굴, 재실 detector와 `VisionStateService`를 연결한다.
- WHEP decode와 추론이 FastAPI·Desk 제어 이벤트 루프를 막지 않는지 확인한다.

완료 조건: 최신 Vision 상태와 미리보기를 제공하고, 카메라/추론 오류를
`UNKNOWN` 또는 오류 상태로 안전하게 표시한다.

## 4단계: 자동화와 외부 표시

- `AutomationService`로 Vision·프로필·Desk 상태를 조합한다.
- `controlMode`와 `activityMode`를 분리하고 active 작업 모드로 높이·WLED를 적용한다.
- 불확실·다중 사용자·오래된 Vision 상태에서 STOP하는 규칙을 구현한다.
- WLED와 운영용 MQTT 상태 발행을 연결한다.

완료 조건: 등록/미등록 사용자, 자세 전환, 퇴장, 수동 조절, 오류 중단 흐름을
통합 테스트한다.

## 5단계: 실물 검증과 운영 정리

- ESP32와 Arduino 펌웨어를 빌드하고 계약 호환을 확인한다.
- 실제 이동은 제한된 범위에서 UP, DOWN, STOP, 목표 도달, 센서 단절을 검증한다.
- 시작·종료·장애 대응·환경 변수를 README와 운영 문서에 확정한다.

## 계속 유지할 테스트 구분

| 종류 | 대상 |
| --- | --- |
| 단위 테스트 | 높이 범위, 상태전이, 안정화, 프로필 정책 |
| 계약 테스트 | HTTP 요청/응답, MQTT JSON, 토픽 |
| 통합 테스트 | 앱 lifespan, 가짜 MQTT/시리얼/카메라 |
| 실물 검증 | ESP32 펄스, Arduino 높이, STOP, 장애 조건 |

실물 책상 이동은 단위·계약·통합 테스트를 통과한 뒤에만 수행한다.

각 단계의 실행 가능한 세부 체크리스트는 [작업 목록](../tasks/README.md)을
기준으로 한다. roadmap은 큰 단계와 목표를 설명하고, `docs/tasks/`는 실제 작업
순서와 완료 증거를 관리한다.
