# 09. 통합·실물 검증

## 사용자 결과

다른 개발자가 문서만 보고 전체 시스템을 시작해 profile 생성, 얼굴 등록, AUTO 자세 이동,
MANUAL preset과 Voice 응답을 시연할 수 있다. 장치나 네트워크가 끊겼을 때 확인할 상태와
복구 절차가 있으며, 책상은 모든 필수 장애 시나리오에서 안전하게 정지한다.

## 진행 방식

이 task는 01~08 완료 뒤 테스트를 한 번 몰아서 작성하는 단계가 아니다. 각 기능 task에서
자동 검증을 누적하고, 여기서는 실제 EMQX·MediaMTX·카메라·Arduino·ESP32·WLED·오디오를
연결한 계약과 실물 동작을 최종 확인한다.

실제 책상 이동은 다음 순서를 지킨다.

```text
순수 상태전이 테스트
  → fake adapter 통합 테스트
  → relay 분리·비이동 bench
  → 제한 높이·짧은 이동
  → 전체 사용자 시나리오
```

## 선행 조건

- 01~08의 관련 자동 테스트와 완료 조건 충족
- 테스트에 사용할 firmware version, 배선과 환경 설정 기록
- ESP32·높이 센서·STOP timeout과 이동 상·하한 재확인
- 즉시 사용할 물리 전원 차단 또는 별도 STOP 수단 준비
- 주변 사람·장애물을 제거하고 제한된 이동 범위 합의

## 기존 task에서 이관한 미완료 검증

기존 기반 작업 문서를 제거하면서 다음 항목을 완료된 것으로 오인하지 않는다.

- [ ] logic analyzer로 relay GPIO의 both-OFF 30ms와 50/500ms pulse edge 실측
- [ ] 실제 ESP32 MQTT session arming과 retained height 무시 확인
- [ ] live height lease 만료 시 relay STOP 확인
- [ ] Wi-Fi·broker 단절과 재연결 중 fail-closed STOP 확인
- [ ] 실제 ESP32 수신 시각 기준 pulse 갱신 간격과 무중단 연장 확인
- [ ] 브라우저 HOLD 요청이 release·단절될 때 watchdog과 ESP32 timeout STOP 확인
- [ ] safe MQTT integration과 제한된 실제 책상 목표 이동 확인

## 자동 검증 묶음

### 코드와 계약

- [ ] Python 전체 unit·integration test와 compile/static 검사를 실행한다.
- [ ] React TypeScript 검사와 production build를 실행한다.
- [ ] HTTP request/response, 오류 코드와 stale `sessionId` 계약을 확인한다.
- [ ] MQTT command/status payload, QoS와 non-retained 명령을 firmware와 대조한다.
- [ ] SQLite migration·rollback·foreign key와 profile 연관 삭제를 검증한다.
- [ ] server restart 후 current user, mode와 자동 intent가 복원되지 않는지 확인한다.

### lifecycle과 장애 주입

- [ ] resource 시작 순서와 일부 시작 실패 시 역순 종료를 확인한다.
- [ ] EMQX·MediaMTX·WLED·OpenAI 단절과 복구를 각각 시험한다.
- [ ] user/posture 카메라와 Arduino·오디오 장치를 각각 제거·복원한다.
- [ ] Vision model 예외와 느린 추론 중 health·Dashboard·STOP 응답을 측정한다.
- [ ] FastAPI 정상 종료, task 실패와 강제 종료 뒤 ESP32 독립 timeout STOP을 확인한다.
- [ ] 장시간 실행에서 frame, Assistant turn과 background task memory 누적을 확인한다.

## 성능·안전 기록

구현 전 임의 숫자를 완료 기준으로 만들지 않는다. 실제 장치와 사용자 경험에 필요한 허용
기준을 각 기능 task에서 정하고, 여기서 다음 값을 같은 조건으로 반복 측정한다.

- HTTP STOP 접수와 MQTT STOP 발행 시간
- ESP32 명령 수신부터 GPIO OFF까지의 시간
- height 관측 age와 lease 만료 시간
- 카메라 frame age, detector·상태 안정화 지연
- 앉음·섬 유지 완료부터 Desk 목표 접수까지의 시간
- wake word부터 음성 응답 시작 및 Dashboard 상세 응답까지의 시간
- idle·추론·Voice 처리 중 CPU와 memory

측정에는 hardware, firmware, model, 해상도·FPS, network와 sample 수를 함께 기록한다.

## 최종 사용자 시나리오

### 설정과 등록

- [ ] profile 이름·앉은·선 높이와 조명을 저장하고 키·자세 유지 시간 필드가 없는지 확인한다.
- [ ] 사용자 높이 preset을 생성·수정·삭제한다.
- [ ] 얼굴 등록을 취소·재시도하고 성공 뒤 background 재인식으로 session을 만든다.
- [ ] profile 설정 화면을 열어도 current user와 Desk가 바뀌지 않는다.

### 자동·수동 제어

- [ ] 얼굴로 사용자가 확정되면 새 `sessionId`와 AUTO가 표시된다.
- [ ] 등록 얼굴 확정 없이 상단 몸체 또는 얼굴과 하단 하체·자세가 3초 안정화되면 익명 session이 표시된다.
- [ ] 익명 최초 AUTO가 2초 더 기다린 뒤 앉음 75cm·섬 110cm 목표를 선택한다.
- [ ] 익명 AUTO 이동 중 얼굴이 식별되면 새 등록 session과 profile 목표로 안전하게 교체된다.
- [ ] 앉음·섬을 유지하면 각 profile 높이로 한 번 이동한다.
- [ ] preset 클릭 후 MANUAL로 유지되고 자세 변화가 목표를 덮어쓰지 않는다.
- [ ] 같은 session에서 사용자가 AUTO를 다시 선택하면 STOP과 fresh 자세 5초 확인 뒤 동작한다.
- [ ] HOLD release·page hide·network 단절에서 제한 시간 안에 정지한다.

### 사용자·Vision 경계

- [ ] 얼굴 일시 누락은 단일 재실에서 session을 유지하고, 고품질 미등록 얼굴 3초는 익명으로 전환한다.
- [ ] 다중·count 불일치는 AUTO만 STOP하고 Dashboard 수동 제어는 계속 동작한다.
- [ ] fresh VACANT 30초 뒤 75cm park하며 사람 후보·수동 명령에서 즉시 취소한다.
- [ ] A→B 교대와 A Dashboard의 오래된 명령이 B에게 적용되지 않는다.
- [ ] 다중 사용자, 카메라 count 불일치와 주변 통행에서 새 자동 목표가 차단된다.
- [ ] 이탈, frame stale, sensor·MQTT·relay 오류에서 진행 이동이 정지한다.
- [ ] 서버 재시작 후 fresh 재실 session 또는 fresh VACANT 30초 전에는 자동 이동하지 않는다.

### Voice와 AI

- [ ] WLED와 Voice가 설정 분기 없이 lifecycle에서 시작된다.
- [ ] Voice가 current user의 기억만 사용하고 사용자 없음에서는 개인 기억을 사용하지 않는다.
- [ ] 익명 Voice history는 session 안에서만 유지되고 등록 전환·종료에서 폐기된다.
- [ ] 사용자 교대 중 Assistant turn이 잘못된 profile 기억에 저장되지 않는다.
- [ ] 사용자 교대·session 종료 즉시 이전 AI 상세 응답이 메인 화면에서 사라진다.
- [ ] profile 삭제가 장기 기억·얼굴·preset을 함께 삭제하고 기억 삭제 실패에서는 DB를 보존한다.
- [ ] 같은 turn의 음성 응답과 Dashboard 상세 응답이 일치한다.
- [ ] WLED·Desk tool 실패가 기능별 오류로 표시되고 안전 경계를 우회하지 않는다.

## 운영 문서 산출물

- 필수 service와 외부 인프라의 시작 순서
- 운영 환경변수, model·effect·firmware 파일 목록
- 정상 상태와 기능별 degraded/blocked 확인 방법
- 장치별 단절·복구와 안전 STOP 확인 절차
- DB backup, migration 실패와 profile·얼굴 데이터 복구 절차
- 시연 순서, 알려진 제한과 아직 보장하지 않는 조건
- 자동 테스트 결과와 실물 검증 일시·환경·측정 기록

## 완료 조건

- 최종 사용자 시나리오를 문서 순서대로 재현할 수 있다.
- 모든 필수 장애 시나리오에 자동 테스트 또는 실물 측정 증거가 있다.
- 미완료 실물 검증과 알려진 제한을 완료로 숨기지 않고 명시한다.
- 서버·Dashboard·firmware 문서와 실제 설정·API·상태 이름이 일치한다.
- 테스트 종료 후 relay가 STOP이고 background process·thread·장치 handle이 남지 않는다.
