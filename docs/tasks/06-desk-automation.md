# 06. 책상 자동화

## 사용자 결과

단일 재실·자세가 안정화되면 등록 또는 익명 session을 기본 `AUTO`로 시작한다. 등록 사용자는
profile 높이, 익명 사용자는 앉음 75cm·섬 110cm를 사용한다. 최초 목표는 session 시작 뒤
2초 동안 조건이 유지돼야 한다. preset, 직접 목표 또는 HOLD를 사용하면 `MANUAL`로 전환되고
명시적으로 AUTO를 다시 선택하기 전까지 자세 변화가 책상 목표를 덮어쓰지 않는다.

## 현재 상태

- `DeskController`는 목표 이동, HOLD, STOP, sensor freshness와 relay 안전을 구현한다.
- `/api/control`과 `/api/target`은 현재 Dashboard에서 `DeskController`로 직접 위임한다.
- profile에는 앉은·선 높이가 있지만 사용자 session은 아직 없다. 자세 전환 시간은 profile
  필드로 추가하지 않고 전체 고정 5초를 사용한다.
- `AutomationService`, server mode, generation과 차단 이유 API가 없다.
- 화면의 자동화 ON과 5초 표시는 실제 서버 상태가 아닌 placeholder다.

## 책임 경계

| 구성요소 | 책임 |
| --- | --- |
| Identity/Vision | 신원·재실·자세와 freshness 관측 |
| CurrentUser session | 현재 profile과 session 연속성 |
| AutomationService | mode, 자세 안정화, 사용자 의도와 목표 선택 |
| DeskController | 높이·relay 안전, 실제 목표/HOLD/STOP 실행 |
| Dashboard | 명령 전송과 snapshot 표시 |
| Agents SDK Desk function tool | turn의 `sessionId`를 전달하고 공개 command만 호출 |

`AutomationService`는 relay pulse를 직접 보내지 않는다. `DeskController`는 얼굴이나 profile을
해석하지 않으며 전달받은 목표와 기존 물리 안전만 검증한다.
Agents SDK function tool도 `DeskController`나 relay를 직접 호출하지 않고 Dashboard와 같은
`AutomationService` command 경계를 사용한다. AutomationService에는 Agents SDK 타입을
유입하지 않는다.

## 상태와 공개 snapshot

task 01 계약을 기준으로 최소한 다음을 제공한다.

- 현재 `sessionId`, 등록·익명 높이 정책과 `AUTO`/`MANUAL` mode 또는 mode 없음
- 자동화 상태(`WAITING_USER`, `OBSERVING`, `MOVING`, `MANUAL`, `BLOCKED`, `PARK_WAITING`, `PARKING` 등)
- 자세 후보, 안정화 시작·완료 시각과 목표 높이
- 마지막 mode 전환 시각·이유·명령 출처
- 새 자동 목표를 막는 구체적인 차단 코드
- 실행 generation 또는 command revision
- 최초 이동 due 시각, park due 시각과 intent source

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

- [ ] task 05가 새 등록 또는 익명 session을 발행하면 그 session의 기본 mode를 AUTO로 만든다.
- [ ] AUTO 진입 시 이전 자세 후보·완료 목표와 timer를 초기화한다.
- [ ] 새 session의 최초 목표는 이미 완료된 3초 session 후보를 이어 받아 2초만 추가 지연한다.
- [ ] 최초 목표 이후 자세 전환과 명시적 AUTO 재활성화에는 등록·익명 구분 없이 설정의 고정
  5초를 적용한다.
- [ ] 익명 자세 목표는 앉음 75cm·섬 110cm로 선택한다.
- [ ] 현재 높이가 목표 허용 오차 안이면 새 이동을 만들지 않는다.
- [ ] 같은 자세·같은 목표를 frame마다 반복 설정하지 않는다.
- [ ] 사용자 교대, 이탈, 미등록 얼굴 전환, 다중 사용자와 freshness 만료를 task 01 결정표대로
  STOP 또는 BLOCK 처리한다.
- [ ] 익명 AUTO 중 등록 identity 확정은 현재 목표를 안전하게 profile 목표로 교체한다.
- [ ] fresh VACANT 30초 뒤 75cm PARK를 만들고 사람 후보·수동 명령에서 취소한다.

### MANUAL과 명령

- [ ] preset·직접 목표·HOLD의 MANUAL 전환과 기존 자동 generation 무효화를 직렬화한다.
- [ ] 사용자 STOP과 안전 STOP의 mode 결과를 구분하고 session 검증보다 먼저 처리한다.
- [ ] 같은 session의 명시적 AUTO 재활성화는 진행 이동 STOP → 후보 초기화 → AUTO 전환 →
  fresh 자세 5초 확인 순서로 처리한다.
- [ ] mode·preset·Agents SDK Desk function tool의 `expectedSessionId`를 command lock 안에서
  비교한다.
- [ ] function tool은 model이 tool call을 만든 시점이 아니라 AutomationService command가
  실제 부작용을 실행하기 직전에 turn 시작 `sessionId`를 재검증한다.
- [ ] 등록 자세 preset은 profile 값, custom은 소유권, 익명 자세는 기본값을 서버에서 조회한다.
- [ ] session 없는 HOLD·직접 목표·STOP을 session이나 mode 생성 없이 처리한다.
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
- Agents SDK function tool adapter와 Agent prompt 구현(task 08 범위). 이 task는 호출할 공개
  AutomationService 계약과 안전 검증까지만 소유한다.
- 시간 경과에 따른 자동 `MANUAL → AUTO` 전환

## 핵심 자동 검증

- 새 session은 AUTO이고 2초 최초 지연을 거친다. 익명→등록 identity 확정은 연속된 fresh 자세를
  이어 받아 profile 목표로 교체할 수 있다.
- 익명 앉음·섬은 75/110cm 목표를 정확히 한 번 만들고 custom preset을 제공하지 않는다.
- 앉음→섬과 섬→앉음은 고정 5초 뒤 목표를 한 번만 설정한다.
- 흔들리는 자세, 다중·count 불일치와 stale frame은 timer를 잘못 이어가지 않는다.
- preset·직접 목표·HOLD는 먼저 MANUAL로 바뀌고 이전 자동 목표를 무효화한다.
- MANUAL에서는 자세가 바뀌어도 자동 목표가 생성되지 않는다.
- 이전 session ID, 다른 profile preset과 오래된 generation을 거절한다.
- 사용자 교대와 경합한 Desk function tool은 실제 목표를 만들기 전에 session 불일치로
  거절하고, STOP은 session 불일치와 무관하게 처리한다.
- 사용자가 AUTO를 다시 선택하면 기존 이동과 후보를 버리고 fresh 자세 5초를 다시 요구한다.
- Vision·높이·MQTT·relay 오류 및 명령 경합에서 STOP이 우선한다.
- session 없는 HOLD·직접 목표와 STOP이 허용되고 사용자 종속 stale 명령만 거절된다.
- fresh VACANT 30초 전에는 park하지 않고 사람 후보·수동 명령·오류가 PARK를 STOP한다.

## 실물 검증 전 조건

- fake Vision·profile·Desk로 전체 mode 전이표 테스트 통과
- relay 분리 또는 비이동 환경에서 발행할 목표·STOP 순서 확인
- 높이 상·하한, sensor stale와 ESP32 pulse timeout 재확인
- 테스트 중 즉시 사용할 별도 물리 STOP 수단과 이동 범위 제한

## 완료 조건

- 등록·익명 사용자의 AUTO 앉음·섬 전환과 MANUAL preset 흐름이 서버만으로 동작한다.
- Dashboard가 닫혀도 mode와 자동화가 유지되며 여러 클라이언트가 같은 snapshot을 본다.
- 불확실성·사용자 교대·수동 개입·장치 오류에서 진행 중 이동과 새 목표가 차단된다.
- 모든 실제 이동 요청이 `DeskController`를 거치고 STOP 우선순위를 보존한다.
