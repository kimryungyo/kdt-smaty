# 구현 순서

2~3개월 안에 시연 가능한 결과를 만드는 순서다. 각 단계는 이전 단계의 안전
검증이 통과한 뒤 진행한다.

## 0단계: 기본 골조 — 완료

- FastAPI lifespan, `AppContainer`, Settings와 `TaskManager`를 구현했다.
- React + TypeScript + Vite 골조와 FastAPI 정적 제공을 구현했다.
- live/ready API와 기본 단위·통합 테스트를 구현했다.

완료 조건: worker 하나에서 앱이 시작·종료되고 React build와 health API가 동작한다.

## 1단계: 안전한 책상 제어 — 다음 작업

- `DeskHeightMonitor`, `SegmentDecoder`, `RelayClient`, `DeskController`를 구현한다.
- 기존 MQTT 토픽·JSON·높이 범위·ESP32 보호를 그대로 연결한다.
- 목표, HOLD, STOP, 센서 만료, 서버 종료 단위 테스트를 만든다.

완료 조건: 센서 없이 릴레이를 켜지 않으며, 모든 종료 경로에서 STOP 요청을 한다.

## 2단계: Dashboard와 프로필

- `ProfileRepository`, `DashboardService`, FastAPI 라우트를 구현한다.
- 상태 조회, 수동 제어, 목표 설정, 프로필 CRUD를 연결한다.
- 기존 HTTP API를 보존할지 새 API로 정리할지 이 단계에서 확정하고 계약 테스트를 만든다.

완료 조건: 브라우저에서 실제 높이·Desk 상태를 보고 안전한 수동/목표 제어를 할 수 있다.

## 3단계: Vision 파이프라인

- 카메라별 `CameraFrameSource`와 `FramePreprocessor`를 구현한다.
- 자세, 얼굴, 재실 detector와 `VisionStateService`를 연결한다.
- 카메라 읽기와 추론이 FastAPI·Desk 제어 이벤트 루프를 막지 않는지 확인한다.

완료 조건: 최신 Vision 상태와 미리보기를 제공하고, 카메라/추론 오류를
`UNKNOWN` 또는 오류 상태로 안전하게 표시한다.

## 4단계: 자동화와 외부 표시

- `AutomationService`로 Vision·프로필·Desk 상태를 조합한다.
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
