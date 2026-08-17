# 02. 서비스 수명주기와 준비 상태

## 사용자 결과

서버는 장치 하나가 빠졌다는 이유로 설정 화면과 상태 조회까지 모두 막지 않는다. 대신 현재
가능한 기능을 구분해 표시하고, 실제 책상 이동은 Arduino 높이와 Wi-Fi/MQTT ESP32 relay가
모두 준비된 경우에만 허용한다. WLED와 Voice 장애는 해당 기능만 `DISABLED`, `DEGRADED`
또는 `ERROR`로 표시하며 Desk·profile·Dashboard를 중단하지 않는다.

Voice 구현은 legacy `AssistantService`가 아니라
[Agents SDK 음성 파이프라인 전환 결정](../architecture/agents-sdk-voice-pipeline.md)의
`AgentsVoiceRuntime`을 대상으로 한다. 폐기 예정 gateway와 수동 tool loop를 새 lifecycle
경계로 굳히지 않는다.

## 현재 상태

- SQLite, MQTT, Arduino 높이 입력과 `DeskController`는 기본 lifecycle에 등록된다.
- MQTT·높이·Desk의 critical task 종료는 애플리케이션 readiness를 내린다.
- WLED와 Voice는 각각 `enabled` 설정일 때만 생성되며 장치 장애는 기능 snapshot으로 표현한다.
- profile CRUD, 작업 모드 CRUD와 `/api/status`는 전역 application readiness와 무관하게
  관련 저장소 또는 snapshot이 준비되면 응답한다.
- 현재 Voice는 legacy 경로다. Agents SDK core 전환을 먼저 끝낸 뒤 최종 lifecycle을 연결한다.

## 확정 분류

“필수”를 서버 시작, 책상 이동, 제품 부가 기능에 동일하게 적용하지 않는다.

| 분류 | 대상 | 실패 처리 |
| --- | --- | --- |
| 애플리케이션 시작 필수 | 유효한 설정, SQLite schema·저장소, lifecycle 조립 | 시작 실패와 역순 정리 |
| 책상 이동 필수 | MQTT 연결, fresh Arduino 높이, fresh·ready ESP32 relay 상태 | 서버는 유지하되 AUTO·PARK·수동 이동 차단, STOP은 계속 접수 |
| AUTO 추가 필수 | fresh Vision, 단일 재실, 귀속 가능한 자세 | AUTO만 차단, 명시적 수동 제어는 장치가 준비되면 허용 |
| 선택 제품 기능 | WLED, Voice/OpenAI·오디오, Voice debug | 해당 기능만 `DISABLED`/`DEGRADED`/`ERROR`, 핵심 readiness와 Desk 제어에 영향 없음 |

현재 운영 ESP32 transport는 Wi-Fi/MQTT다. Arduino 높이 입력 USB serial은 별도 센서 연결이고,
MQTT→USB-serial bridge는 lifecycle·readiness·복구 대상에 포함하지 않는다.

`enabled=false`는 선택 기능의 정상적인 `DISABLED` 상태다. `enabled=true`인데 필수 정적 설정,
dependency 또는 model 파일이 잘못된 경우에는 조용히 기능을 생략하지 않고 구체적인 구성
오류를 낸다. 시작 후 WLED, 오디오 또는 OpenAI가 일시적으로 끊기면 기능별 상태와 재시도로
표현한다.

## readiness 사용 원칙

`/health/live`와 `/health/ready`는 process·lifecycle 감시용이다. 이를 모든 API의 공통 권한
검사처럼 사용하지 않는다.

- profile CRUD, current-user·Vision·자동화·장치 상태 조회는 관련 저장소나 service가
  사용 가능하면 전역 readiness가 낮아도 응답한다.
- AUTO·PARK·HOLD·직접 목표는 실행 직전에 필요한 Vision·height·MQTT·relay 상태를 각각
  검사한다. 전역 readiness를 통과했다는 이유로 기능별 안전 검사를 생략하지 않는다.
- STOP/CANCEL은 사용자 session, Vision과 전역 readiness 때문에 거절하지 않는다. 가능한
  transport로 즉시 STOP을 시도하고 결과를 상태에 남긴다.
- 선택 기능 장애는 기능별 snapshot으로 표시하며 `/health/ready`를 내리지 않는다.

이는 안전 검사를 우회하는 것이 아니다. 관련 없는 전역 검사로 profile 설정까지 막지 않고,
물리 명령 지점에서 더 구체적인 안전 조건을 반드시 확인하는 방식이다.

## 구현 단계

### Agents SDK 선행 정리

- [ ] task 08의 Agents SDK core 전환으로 legacy gateway·수동 tool loop를 제거한다.
- [ ] `AgentsVoiceRuntime`, Agent factory와 SDK 대화 session adapter의 최종 생성 경계를 정한다.
- [ ] SDK 객체를 `VoiceService`와 사용자 session service에 흩어 놓지 않는다.

### 설정과 조립

- [ ] SQLite·MQTT·height·relay·Desk와 WLED·Voice의 분류를 container 타입에 반영한다.
- [ ] WLED·Voice `enabled=false`는 정상 `DISABLED`, 활성화된 기능의 잘못된 정적 구성은 명시적
  오류가 되게 한다.
- [ ] Voice debug만 운영 Voice와 독립적인 개발용 선택 기능으로 유지한다.
- [ ] dependency 생성 실패를 삼키지 않고 resource 이름과 원인을 보존한다.
- [ ] fake WLED·Voice·height·relay를 주입할 수 있는 현재 테스트 경계를 유지한다.

### lifecycle과 상태

- [x] 현재 lifecycle 등록 resource의 startup order와 shutdown order 역순 종료를 검증한다.
- [x] 일부 시작 실패 시 이미 시작한 resource가 역순으로 정확히 한 번 종료되고 목록에서 제거됨을 검증한다.
- [ ] MQTT·Arduino·ESP32 단절은 Desk 기능별 `BLOCKED` 근거가 되고 STOP을 시도하게 한다.
- [ ] WLED·오디오·OpenAI runtime 단절은 해당 기능 상태와 복구로만 나타나게 한다.
- [x] profile·작업 모드 CRUD와 `/api/status`에서 전역 readiness guard를 제거하고, 저장소 오류만 `503`으로 변환한다.

### 문서와 운영

- [ ] 개발·운영 환경변수에서 핵심 service와 선택 기능을 구분한다.
- [ ] Arduino·ESP32·WLED·오디오·OpenAI 단절의 확인·복구 절차를 각각 기록한다.
- [ ] 운영 문서에서 serial bridge를 시작하거나 확인하도록 안내하지 않는다.

## 제외 범위

- WLED effect 자체의 세부 정책과 작업 모드별 조명 적용(task 03·06 범위)
- VoicePipeline과 Agent workflow 내부 구현(task 08 범위)
- 범용 health framework, service registry 또는 별도 supervisor 도입
- 장치 hot-plug를 위해 프로세스를 여러 개로 분리하는 작업

## 검증

- SQLite schema가 잘못되면 시작이 실패하고 이미 시작한 resource가 남지 않는다.
- MQTT task 또는 Desk critical task가 종료되면 readiness와 STOP 경로에 반영된다.
- Arduino 높이 또는 ESP32 상태가 stale이면 모든 이동이 차단되지만 profile CRUD와 상태
  조회는 가능하다.
- Vision 장애는 AUTO만 차단하고 장치가 준비된 수동 제어와 STOP은 유지한다.
- WLED·Voice가 비활성 또는 장애여도 Desk·profile·Dashboard는 정상 동작한다.
- 활성화한 WLED·Voice의 잘못된 정적 구성은 조용한 기능 누락으로 처리되지 않는다.
- STOP/CANCEL은 stale session과 기능별 degraded 상태에서도 가능한 경로로 접수된다.

## 완료 조건

- 시작 실패, Desk 이동 차단과 선택 기능 degraded가 코드·health·Dashboard에서 구분된다.
- Arduino·ESP32가 준비되지 않은 상태에서 책상이 움직이지 않는다.
- WLED·Voice 장애가 핵심 기능을 막거나 작업 모드·책상 정책을 rollback하지 않는다.
- 전역 readiness가 profile 설정과 상태 조회의 불필요한 공통 차단 장치로 사용되지 않는다.
- 실패·종료 후 background task, 오디오 stream, HTTP client와 장치 handle이 남지 않는다.
