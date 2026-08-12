# 08. AI 사용자 문맥

## 사용자 결과

Voice는 얼굴로 확정한 현재 사용자의 기억과 profile 설정만 사용한다. 미등록·불확실·다중
사용자 상태에서는 이전 사람의 기억을 가져오거나 저장하지 않는다. 음성 답변과 함께 필요한
상세 정보는 같은 Assistant turn으로 Dashboard에 표시된다.

## 현재 상태

- wake word, 녹음, Assistant 호출, playback과 WLED function tool 기반이 구현돼 있다.
- Voice는 아직 서버 current user session을 입력으로 받지 않는다.
- profile별 장기 기억 service와 저장 정책은 설계 문서 수준이다.
- Dashboard에 Assistant turn이나 화면용 상세 응답을 전달하는 API가 없다.
- WLED tool은 있지만 Desk 사용자 명령 tool은 자동화 경계에 연결되지 않았다.

## 사용자 귀속 원칙

- Dashboard에서 편집한 profile은 Voice 사용자 문맥이 아니다.
- fresh하고 사용 가능한 current user session에서만 `profile:<profile_id>` 기억을 사용한다.
- Assistant turn 시작 시 `sessionId`와 profile ID를 캡처한다.
- turn 완료 시 session이 바뀌거나 정책상 불확실해졌다면 이전 profile 장기 기억에 저장하지
  않는다.
- 화면 응답 delivery ID와 기억 user ID를 혼동하지 않는다.
- 얼굴 식별이 없더라도 일반 비개인화 질문을 허용할지는 task 01과 Voice 정책에서 별도로
  정하고, 허용하더라도 사용자별 기억은 사용하지 않는다.

## Assistant turn 모델

구체적인 전송 방식 전에 서버가 소유할 최소 turn 상태를 정의한다.

| 필드 | 목적 |
| --- | --- |
| `turnId` | 음성·화면 응답과 tool 실행을 하나로 연결 |
| `sessionId` | turn 시작 시 사용자 session 또는 `null` |
| `profileId` | 개인화에 사용한 profile 또는 `null` |
| 상태 | listening, processing, responding, succeeded, failed 등 |
| 화면 응답 | 제목·요약·상세 내용 또는 구조화된 결과 |
| 시각 | 시작·갱신·완료와 freshness |
| 오류 | 사용자에게 노출 가능한 실패 코드와 안내 |

Dashboard 전송은 polling, SSE 또는 다른 단순 방식을 비교해 현재 단일 서버에 가장 작은
구조를 선택한다. 여러 과거 대화를 관리하는 chat 제품을 만들지 않고 최신 turn과 필요한
짧은 이력만 제공한다.

## 구현 단계

### 현재 사용자 연결

- [ ] VoiceService 또는 AssistantService가 current user snapshot을 안전하게 읽도록 주입한다.
- [ ] turn 시작 시 사용자 session을 캡처하고 transcript·tool·memory 동작에 전달한다.
- [ ] session 없음·재검증·교대 상태의 비개인화 동작과 memory 차단을 구현한다.
- [ ] 서버 재시작 또는 session 종료 후 이전 profile 문맥이 process state에 남지 않게 한다.

### 기억 경계

- [ ] `profile:<profile_id>` namespace를 사용하는 memory service 경계를 구현한다.
- [ ] 검색·저장할 정보, 최대 결과 수, timeout과 실패 fallback을 정한다.
- [ ] turn 완료 시 같은 session인지 다시 확인한 뒤에만 사용자 기억을 저장한다.
- [ ] profile 삭제 시 장기 기억 삭제·보존 정책과 운영 절차를 확정한다.
- [ ] transcript, 사용자 ID와 기억 내용의 로그·보존·민감정보 범위를 문서화한다.

### Dashboard 응답

- [ ] 음성 문장과 화면용 상세 응답을 하나의 Assistant 결과로 생성한다.
- [ ] 최신 turn snapshot 또는 event API와 Dashboard 표시를 구현한다.
- [ ] 늦게 완료된 과거 turn이 새 turn 화면을 덮어쓰지 않도록 `turnId`·sequence를 사용한다.
- [ ] 사용자 교대 시 이전 turn을 계속 표시할지 접을지와 개인 정보 노출 규칙을 적용한다.
- [ ] tool 실행 중·성공·부분 실패를 음성과 화면에서 모순 없이 표현한다.

### tool 정책

- [ ] WLED tool은 WLED public service만 호출하고 내부 client 상태를 우회하지 않게 유지한다.
- [ ] Desk tool을 추가한다면 `AutomationService`의 session·mode·안전 검증을 반드시 통한다.
- [ ] tool 호출이 사용자 교대와 경합하면 캡처한 session ID를 서버에서 재검증한다.
- [ ] STOP 성격의 안전 명령과 개인화 명령의 권한·문맥 차이를 유지한다.

## 제외 범위

- 범용 채팅 서비스, 장기 대화 검색 UI와 여러 기기 동기화
- 얼굴을 인증 수단으로 사용하는 민감한 외부 계정 작업
- Dashboard가 current user나 Voice session을 직접 선택하는 기능
- Assistant가 `DeskController` 또는 relay를 직접 호출하는 구조

## 검증

- A와 B profile의 기억이 서로 검색·저장되지 않는다.
- current user가 없는 turn은 이전 사용자의 memory와 profile 설정을 사용하지 않는다.
- A session에서 시작해 B session에서 끝난 turn이 A나 B 기억에 잘못 저장되지 않는다.
- Dashboard의 editing profile 변경이 Voice 사용자와 memory namespace를 바꾸지 않는다.
- 늦은 과거 turn 응답이 최신 Dashboard 응답을 덮어쓰지 않는다.
- 같은 turn의 음성·화면 응답과 tool 결과가 일관된 성공·오류를 나타낸다.
- memory 또는 Dashboard delivery 장애가 Voice 기본 응답과 안전 STOP 처리를 막지 않는다.

## 완료 조건

- Voice와 Dashboard AI 응답이 같은 서버 current user 근거와 `turnId`를 사용한다.
- 사용자 없음·교대·불확실 상태에서 이전 사용자의 기억과 개인화가 재사용되지 않는다.
- profile별 기억의 저장·검색·삭제 및 로그 범위가 코드와 문서에 일치한다.
- Assistant의 장치 tool이 WLED·AutomationService의 공개 정책 경계를 우회하지 않는다.
