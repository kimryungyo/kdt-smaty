# 06. 책상 자동화

## 사용자 결과

얼굴로 새 사용자가 확정되면 해당 session은 기본 `AUTO`로 시작한다. 사용자가 앉거나 일어선
상태를 설정 시간 동안 유지하면 저장한 자세 높이로 한 번 이동한다. preset, 직접 목표 또는
HOLD를 사용하면 `MANUAL`로 전환되고 명시적으로 AUTO를 다시 선택하기 전까지 자세 변화가
책상 목표를 덮어쓰지 않는다.

## 현재 상태

- `DeskController`는 목표 이동, HOLD, STOP, sensor freshness와 relay 안전을 구현한다.
- `/api/control`과 `/api/target`은 현재 Dashboard에서 `DeskController`로 직접 위임한다.
- profile에는 앉은·선 높이가 있지만 사용자 session과 자세 유지 시간은 아직 없다.
- `AutomationService`, server mode, generation과 차단 이유 API가 없다.
- 화면의 자동화 ON과 5초 표시는 실제 서버 상태가 아닌 placeholder다.

## 책임 경계

| 구성요소 | 책임 |
| --- | --- |
| Vision | 신원·재실·자세와 freshness 관측 |
| CurrentUser session | 현재 profile과 session 연속성 |
| AutomationService | mode, 자세 안정화, 사용자 의도와 목표 선택 |
| DeskController | 높이·relay 안전, 실제 목표/HOLD/STOP 실행 |
| Dashboard | 명령 전송과 snapshot 표시 |

`AutomationService`는 relay pulse를 직접 보내지 않는다. `DeskController`는 얼굴이나 profile을
해석하지 않으며 전달받은 목표와 기존 물리 안전만 검증한다.

## 상태와 공개 snapshot

task 01 계약을 기준으로 최소한 다음을 제공한다.

- 현재 `sessionId`와 `AUTO`/`MANUAL` mode 또는 mode 없음
- 자동화 상태(`WAITING_USER`, `OBSERVING`, `MOVING`, `MANUAL`, `BLOCKED` 등)
- 자세 후보, 안정화 시작·완료 시각과 목표 높이
- 마지막 mode 전환 시각·이유·명령 출처
- 새 자동 목표를 막는 구체적인 차단 코드
- 실행 generation 또는 command revision

mode와 자동화 상태는 같은 enum으로 합치지 않는다. `AUTO`이면서 Vision 만료로 `BLOCKED`일
수 있고, mode를 잃지 않은 채 관측 복구를 기다릴 수 있기 때문이다. session 종료 시에는
mode 자체를 제거한다.

## 구현 단계

### service와 lifecycle

- [ ] immutable automation snapshot과 전환 원인을 정의한다.
- [ ] Vision·현재 사용자·profile·Desk 의존성을 생성자로 주입한다.
- [ ] 한 `AutomationService`가 background 관측 loop와 사용자 command lock을 소유하게 한다.
- [ ] 자동 intent마다 generation을 부여해 오래된 async 결과가 새 명령을 덮어쓰지 못하게 한다.
- [ ] lifecycle 시작·종료와 container accessor를 연결하고 종료 전에 STOP한다.

### AUTO 정책

- [ ] 새로 확정된 사용자 session에만 기본 AUTO를 생성한다.
- [ ] AUTO 진입 시 이전 자세 후보·완료 목표와 timer를 초기화한다.
- [ ] 동일 session의 fresh 단일 재실과 자세가 profile 유지 시간 동안 이어져야 목표를 만든다.
- [ ] 현재 높이가 목표 허용 오차 안이면 새 이동을 만들지 않는다.
- [ ] 같은 자세·같은 목표를 frame마다 반복 설정하지 않는다.
- [ ] 사용자 교대, 이탈, identity 재검증, 다중 사용자와 freshness 만료를 task 01 결정표대로
  STOP 또는 BLOCK 처리한다.

### MANUAL과 명령

- [ ] preset·직접 목표·HOLD의 MANUAL 전환과 기존 자동 generation 무효화를 직렬화한다.
- [ ] STOP은 session 검증보다 먼저 처리하고 task 01 계약에 따른 mode 결과를 적용한다.
- [ ] 명시적 AUTO 요청은 진행 이동 STOP → 후보 초기화 → AUTO 전환 순서로 처리한다.
- [ ] `expectedSessionId`가 필요한 명령과 현재 session을 command lock 안에서 비교한다.
- [ ] 자세 preset은 현재 profile 값, 사용자 preset은 row 소유권을 서버에서 다시 조회한다.
- [ ] 장치 상태 때문에 수동 명령이 실패한 뒤 mode를 어떻게 유지할지 계약대로 적용한다.
- [ ] 기존 `/api/control`·`/api/target`을 AutomationService 경계로 이동한다.

### 상태·API·관측

- [ ] mode 변경, 자동화 상태와 현재 사용자 합성 preset API를 구현한다.
- [ ] 명령 접수 성공과 실제 목표 도달을 다른 상태로 표현한다.
- [ ] 차단·전환·STOP 이유를 구조화한 로그와 snapshot에 남긴다.
- [ ] 여러 Dashboard의 동시 명령과 background 자세 전이 경합을 결정적으로 처리한다.

## 단계적 활성화

AUTO mode의 기본값과 실제 relay 이동 활성화는 구분한다. 처음에는 detector 결과와 선택할
목표만 기록하는 관측 검증을 수행하고, 상태전이·STOP 자동 테스트와 제한된 장치 검증을
통과한 뒤 실제 `DeskController.set_target()` 호출을 활성화한다. 운영용 사용자 설정으로
AUTO 기본 mode 자체를 끄는 기능을 추가하지 않는다.

## 제외 범위

- `DeskController`의 MQTT wire 계약과 ESP32 firmware 재설계
- Dashboard 화면 레이아웃과 profile 설정 CRUD
- Voice 명령과 Assistant tool 연결
- 시간 경과에 따른 자동 `MANUAL → AUTO` 복귀

## 핵심 자동 검증

- 새 session은 AUTO이며 과거 session의 자세 snapshot으로 즉시 움직이지 않는다.
- 앉음→섬과 섬→앉음은 유지 시간 뒤 목표를 한 번만 설정한다.
- 흔들리는 자세, 얼굴 재검증과 stale frame은 timer를 잘못 이어가지 않는다.
- preset·직접 목표·HOLD는 먼저 MANUAL로 바뀌고 이전 자동 목표를 무효화한다.
- MANUAL에서는 자세가 바뀌어도 자동 목표가 생성되지 않는다.
- 이전 session ID, 다른 profile preset과 오래된 generation을 거절한다.
- AUTO 복귀는 기존 이동과 후보를 버리고 fresh 안정화를 다시 요구한다.
- Vision·높이·MQTT·relay 오류 및 명령 경합에서 STOP이 우선한다.
- STOP 요청은 사용자 없음과 session 불일치에서도 차단되지 않는다.

## 실물 검증 전 조건

- fake Vision·profile·Desk로 전체 mode 전이표 테스트 통과
- relay 분리 또는 비이동 환경에서 발행할 목표·STOP 순서 확인
- 높이 상·하한, sensor stale와 ESP32 pulse timeout 재확인
- 테스트 중 즉시 사용할 별도 물리 STOP 수단과 이동 범위 제한

## 완료 조건

- 등록 사용자의 AUTO 앉음·섬 전환과 MANUAL preset 흐름이 서버만으로 동작한다.
- Dashboard가 닫혀도 mode와 자동화가 유지되며 여러 클라이언트가 같은 snapshot을 본다.
- 불확실성·사용자 교대·수동 개입·장치 오류에서 진행 중 이동과 새 목표가 차단된다.
- 모든 실제 이동 요청이 `DeskController`를 거치고 STOP 우선순위를 보존한다.
