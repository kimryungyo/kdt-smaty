# 앞으로 할 작업

이 디렉터리는 현재 구현 위에 추가할 상위 작업과 완료 기준을 관리한다. 완료된 MQTT,
높이 입력, relay, `DeskController`, 기본 profile CRUD, Dashboard 골조, 카메라 발행·최신
프레임 수집과 Voice/WLED 기반 코드는 새로운 task로 다시 설명하지 않는다. 남아 있는 실물
검증은 [통합·실물 검증](09-system-validation.md)으로 이관한다.

현재 운영 transport는 `main`의 Wi-Fi/MQTT firmware와 서버 계약이다. MQTT→USB-serial
bridge와 `feature/serial-esp32`은 운영·통합 검증 범위에 포함하지 않는다. Arduino 높이
입력 USB serial과 relay 분리 bench용 serial 명령은 ESP32 운영 transport와 구분한다.

단, legacy Voice의 수동 STT→Responses→TTS 구조는 확정적으로 교체하므로 task 08에서
Agents SDK 전환 자체를 예외적으로 소유한다.

각 문서는 구현 직전의 세부 계획이 아니라 다음 내용을 합의하기 위한 작업 경계다.

- 어떤 사용자 결과를 만드는가
- 현재 코드와 무엇이 다른가
- 먼저 확정할 정책과 공개 계약은 무엇인가
- 어느 코드·데이터·화면까지 변경하는가
- 무엇을 의도적으로 다음 단계로 미루는가
- 어떤 자동·실물 증거가 있어야 완료인가

세부 동작의 기준은 [워크플로우 문서](../workflow/README.md)다. 구현 중 정책이나 공개 계약이
바뀌면 관련 workflow를 먼저 또는 같은 변경에서 갱신한다.

Voice runtime의 확정 목표는 [Agents SDK 음성 파이프라인 전환 결정](../architecture/agents-sdk-voice-pipeline.md)을
따른다. 사용자 session·물리 제어·STOP은 task 01 계약이 우선하고, model·STT·TTS·VAD,
function tool과 SDK memory 구현은 Agents SDK 문서가 우선한다.

## Task 목록과 의존성

번호는 책임 영역을 찾기 위한 식별자이며 이번 구현의 엄격한 직렬 순서가 아니다. 특히 legacy
Voice를 새 lifecycle에 굳히지 않기 위해 task 08의 Agents SDK core를 task 02의 최종 Voice
lifecycle 연결보다 먼저 수행한다.

| 순서 | 작업 | 핵심 결과 | 상태 | 선행·비고 |
| ---: | --- | --- | --- | --- |
| 01 | [상태·워크플로우 계약 확정](01-workflow-contracts.md) | 구현 가능한 상태·전이·API 기준 | 완료 | - |
| 02 | [서비스 수명주기와 준비 상태](02-required-services.md) | 시작·이동 필수 조건과 선택 기능 degraded 분리 | 착수 가능 | 08 Agents core 뒤 최종 연결 |
| 03 | [프로필과 작업 모드](03-profile-and-presets.md) | 활동별 앉기·서기 높이와 LED 저장 | 착수 가능 | SQLite v2→v3 |
| 04 | [Vision 관측](04-vision-observation.md) | 재실·자세·인원수 snapshot | 착수 가능 | 실물 ROI·모델 보정만 장치 대기 |
| 05 | [얼굴 식별과 사용자 세션](05-face-identity-session.md) | 얼굴 등록·식별과 서버 현재 사용자 | 대기 | 03·04 공개 계약 |
| 06 | [책상 자동화](06-desk-automation.md) | 제어 방식·작업 모드와 자세 기반 이동 | 대기 | 03·05 |
| 07 | [Dashboard 워크플로우](07-dashboard-workflow.md) | 설정 대상과 현재 사용자 분리 | 대기 | 설정 화면은 03 뒤, 완료는 05·06 뒤 |
| 08 | [Agents SDK 음성과 AI 사용자 문맥](08-ai-user-context.md) | VoicePipeline·session memory·Mem0·화면 응답 | 착수 가능 | Voice core는 즉시, 사용자·tool·화면 연결은 02·05·06·07 |
| 09 | [통합·실물 검증](09-system-validation.md) | 장애·복구·실제 동작 증거 | 대기 | 02~08 기능 구현 |

실제 착수는 08의 Agents SDK core 교체를 먼저 하고 02에서 최종 Voice lifecycle을 연결한다.
02의 readiness·Desk 분류 정리는 Agents core와 병행할 수 있다. 04는 실제 하단 카메라가 없어도 fake frame과
detector adapter로 snapshot·freshness·안정화·API를 먼저 구현할 수 있고, ROI와 threshold
보정만 장치 연결 뒤 완료한다. 03도 04와 병행할 수 있다. 05는
profile 저장과 Vision 관측을 결합하며, 06은 이 사용자 세션을 기준으로 서버 제어 정책을
완성한다. 07의 profile 설정 화면은 03 뒤 먼저 착수할 수 있지만, current user·자동화 화면과
명령까지 완료하려면 05·06이 필요하다. 08의 Agents SDK core 전환은 먼저 진행할 수 있고,
사용자 session·기억·Desk tool·Dashboard 연결은 02·05·06·07의 공개 계약이 준비된 뒤 완성한다.

키 필드는 제거하고 자세 전환 확인 시간은 전체 고정 5초로 사용한다. profile 삭제 시 장기
기억까지 삭제하며, session 교대·종료 시 이전 AI 상세 응답은 즉시 숨긴다. 작업 모드 이름
정규화와 SQLite cascade 구현 방식, embedding 직렬화 형식, detector threshold는 해당 task에서
기술 검증으로 결정한다. 얼굴 등록은 profile당 서로 다른 시점의 embedding 3~5개를 개별
저장하고, Dashboard AI 응답은 최신 turn 하나를 polling한다.

09는 마지막에 한 번만 수행하는 작업이 아니다. 각 단계에서 자동 검증을 누적하고, 실제
장치가 필요한 항목만 최종 단계에서 제한적으로 실행한다.

## 현재 구현 기준선

- MQTT, Arduino 높이 입력과 ESP32 relay 계약이 구현돼 있다.
- `DeskController`가 목표 이동, HOLD, STOP과 기본 안전 정책을 소유한다.
- SQLite version 2 profile CRUD·height cache와 React Dashboard 골조가 구현돼 있다.
- user·workspace·posture 세 카메라 역할의 `CameraPublisher`와 최신 프레임
  `RtspFrameSource`가 구현돼 있다.
- WLED와 Voice는 선택 기능이며 `enabled=false`는 정상 `DISABLED`다. 활성화한 기능의 잘못된
  정적 구성은 명시적으로 실패하고 runtime 단절은 기능별 degraded로 표시한다.
- Voice는 아직 legacy 수동 STT→Responses/tool loop→TTS 경로이며 Agents SDK 전환은 목표
  문서와 별도 기능 브랜치에만 있다.
- 얼굴·재실·자세 추론, 현재 사용자 세션과 `AutomationService`는 아직 없다.
- Dashboard의 `selectedProfile`은 서버 사용자와 무관하지만 현재 화면에서는 사용자처럼
  표시되고 책상·WLED 명령 입력에 사용된다.

## 상태 관리

task 상태는 다음 값 중 하나를 사용한다.

| 상태 | 의미 |
| --- | --- |
| `설계 필요` | 공개 상태나 안전 정책을 먼저 결정해야 함 |
| `대기` | 선행 task 또는 장치 확인이 필요함 |
| `착수 가능` | 범위와 선행 조건이 충족됨 |
| `진행 중` | 코드·문서·검증을 함께 변경 중 |
| `실물 검증 대기` | 자동 검증은 끝났고 장치 검증만 남음 |
| `완료` | 문서의 완료 조건과 증거가 모두 충족됨 |

상태만 바꾸지 않고 완료한 체크 항목, 검증 명령과 남은 위험도 함께 기록한다. 구현 중 큰
정책 결정이 생기면 task 체크박스보다 workflow와 API 계약을 우선 갱신한다.

## 공통 구현 원칙

- 서버가 사용자, Vision, `controlMode`, `activityMode`와 자동화 상태를 소유한다.
- Dashboard는 profile 설정 입력, 명령 전달과 서버 snapshot 표시를 담당한다.
- Dashboard에서 연 profile은 현재 사용자가 아니며 얼굴 인식으로 편집 화면을 바꾸지 않는다.
- Vision, 높이 센서, MQTT 또는 relay 상태가 불확실하면 자동 이동을 허용하지 않는다.
- 등록 session은 기본 작업 모드, 익명 session은 작업 모드 없이 75/110cm AUTO를 사용한다.
- 안정 VACANT 뒤 fresh 30초가 이어진 경우에만 75cm park를 허용한다.
- 모든 책상 이동은 `DeskController`를 통한다. STOP은 사용자 식별이나 session 일치 여부로
  거절하지 않는다.
- Arduino 높이와 Wi-Fi/MQTT ESP32 상태는 이동에 필수다. WLED·Voice 장애는 Desk·profile
  기능을 막지 않는다.
- Agent function tool은 기존 public domain service만 호출하며 SDK 타입이나 tool argument가
  물리 안전·session 검증을 우회하지 않는다.
- 얼굴 원본과 crop은 기본 저장하지 않으며 얼굴 식별을 보안 인증으로 취급하지 않는다.
- 실제 책상 검증은 가짜 어댑터 기반 상태전이와 STOP 테스트를 통과한 뒤 수행한다.
- 새 상위 task나 구조를 추가할 때는 현재 01~09 중 어느 완료 조건으로 다룰 수 없는지 먼저
  확인한다.

## 작업 착수와 커밋

상위 task를 시작할 때는 0.5~2일 정도의 검토 가능한 하위 단계로 나눈다. 일반적인 한 단계는
`모델·저장 → service → API → Dashboard → 문서·검증` 중 하나의 끝나는 흐름을 가진다.

커밋에는 해당 단계의 코드, 테스트와 직접 관련 문서를 함께 포함한다. 실물 검증 결과만
추가할 때는 사용한 firmware·설정·장치 조건과 결과를 문서에 남긴다.

## 관련 문서

- [Dashboard·기능 워크플로우](../workflow/README.md)
- [구현 로드맵](../implementation/roadmap.md)
- [계획 및 설계 가이드](../guides/README.md)
- [컴포넌트 설계](../architecture/component-design.md)
- [Agents SDK 음성 파이프라인 전환 결정](../architecture/agents-sdk-voice-pipeline.md)
- [실행과 동시성](../architecture/runtime-and-concurrency.md)
- [책상 제어와 안전](../architecture/desk-safety.md)
