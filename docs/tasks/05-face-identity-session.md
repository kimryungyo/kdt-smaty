# 05. 얼굴 식별과 사용자 세션

## 사용자 결과

사용자는 profile 설정에서 얼굴을 등록·재등록·삭제할 수 있다. 서버는 Dashboard 선택과
무관하게 background 재실·얼굴 식별로 등록 또는 익명 사용자를 결정하고, 사용자 교대마다
새 `sessionId`를 발급한다. 얼굴이 없어도 단일 재실이면 익명 session을 시작할 수 있다.

얼굴 등록 성공은 현재 사용자 강제 선택이 아니다. 등록 후 일반 background 식별에서 같은
얼굴이 다시 안정적으로 확인돼야 새 사용자 session이 생긴다.

## 현재 상태

- v4 face-embedding repository, fake capture/clock 기반 identity 안정화와 current-user session
  service/API가 구현돼 있다. Dashboard 편집 profile은 현재 사용자와 분리된다.
- 등록 시작·조회·취소·삭제 API의 상태/오류 경계는 있으나 production 얼굴 detector, landmark
  alignment, embedding extractor와 실제 camera 등록은 없다.
- production extractor는 의도적으로 `MODEL_UNAVAILABLE` fail-closed adapter다. 따라서
  repository/API 자동 테스트 통과는 실제 얼굴 등록·식별 완료를 뜻하지 않는다.

## 구성 경계

```text
user-camera latest frame
  → task 04의 fresh 얼굴 검출 box
  → 정렬·품질 검사
  → FaceEmbeddingExtractor
  → FaceRecognizer raw candidate
  → IdentityStateService 안정화
  + Vision 재실·인원수
  → CurrentUserSessionService
```

- 얼굴 detector는 task 04가 한 번만 load한다. 이 task는 fresh box를 소비하며 detector loop를
  중복 실행하지 않는다.
- embedding model은 애플리케이션당 하나만 load하고 등록과 식별이 공유한다.
- model이 동시 호출을 지원하지 않으면 model 경계의 작은 lock으로 직렬화한다.
- 현재 사용자 session은 embedding 저장소나 Dashboard route가 아니라 별도 서버 상태가
  소유한다.
- profile repository는 얼굴을 보고 현재 사용자를 선택하지 않는다.
- `CurrentUserSessionService`는 session ID·종류·profile과 전환 이유만 소유한다. control/activity mode,
  generation, 목표 교체와 STOP은 task 06의 `AutomationService`가 session 전이를 입력으로
  받아 처리한다.
- Voice runtime이 사용자 교대와 경합하지 않도록 원자적 불변 snapshot, 현재 `sessionId`
  검증과 순서가 보장된 변경 event를 제공한다. Agents SDK session·run·TTS 객체는 소유하지
  않는다.

## 저장 설계

profile마다 서로 다른 시점에 채택한 embedding 3~5개를 개별 row로 저장한다. 평균 vector 하나로
합치지 않는다. background 식별은 유효한 표본들과 비교해 profile score를 계산하고, threshold와
best-second margin을 함께 적용한다.

- 각 row는 profile ID, model 이름·version, dimension, normalization 방식, 생성 시각과 vector를 가진다.
- 같은 enrollment에서 수집한 표본 수는 최소 3, 최대 5다. 정확한 채택 수는 품질·일관성 검사로
  결정하되 3개 미만이면 성공 처리하지 않는다.
- 재등록은 새 표본 집합을 완성한 뒤 기존 집합과 한 transaction에서 교체한다.
- model version·dimension·normalization이 다르면 비교하지 않고 재등록 필요 상태로 처리한다.
- binary/blob 직렬화와 profile score 계산식은 공개 동작을 바꾸지 않는 구현 세부다.

얼굴 이미지와 crop은 기본 저장하지 않는다. embedding vector, raw similarity와 내부 threshold는
일반 Dashboard API 및 로그에 노출하지 않는다.

## 구현 단계

### 얼굴 추론 기반

- [ ] 얼굴 detector, landmark 정렬과 embedding model을 선정하고 의존성을 고정한다.
- [ ] 얼굴 크기, blur, 가림, 각도와 밝기의 품질 거절 기준을 정의한다.
- [ ] executor에서 embedding을 추출하고 model load·동시 호출·종료를 검증한다.
- [x] v4 SQLite 표본 집합을 3~5개로 원자적 교체·삭제하고 metadata 불일치 표본을 비교에서 제외한다.

### 얼굴 등록 session

- [ ] `WAITING_FACE → CAPTURING → PROCESSING → SUCCEEDED` 상태와 실패 코드를 구현한다.
- [ ] 한 명의 얼굴만 있을 때 서로 다른 시점의 품질 표본을 수집한다.
- [ ] 품질·일관성 검사를 통과한 embedding 3~5개를 개별 row로 원자적으로 저장한다.
- [ ] 표본 간 일관성을 검사하고 다른 profile과 중복되는 얼굴을 거절한다.
- [x] fake-driven 등록 시작·상태 조회·취소·얼굴 삭제 API와 동시 등록 `409` 계약을 구현했다.
  실제 embedding 수집·등록 성공은 production model 연결 뒤 검증한다.
- [ ] 등록·재등록·삭제 시작 시 task 01 계약대로 STOP·자동화 차단·후보 초기화를 요청한다.
- [ ] 취소·실패·서버 종료에서 임시 표본과 background task를 정리한다.

### background 식별

- [ ] best match threshold와 best-second margin을 적용하는 open-set 비교를 구현한다.
- [x] fake clock/capture에서 한 frame 후보와 확정 identity를 분리하고 연속 확인·freshness를 적용한다.
- [ ] 미등록, 다중 얼굴, 낮은 품질과 model 오류를 서로 구분해 내부 상태로 기록한다.
- [ ] 새로운 등록이나 삭제가 진행 중이면 오래된 identity 결과를 발행하지 않는다.
- [ ] 고품질 미등록 얼굴 3초와 단순 `NO_FACE`·낮은 품질을 구분한다.

### 현재 사용자 session

- [x] fake Vision 관측에서 등록·익명 시작, 얼굴 누락·이탈·다중 사용자 전이를 구현했다.
- [x] 새 확정 사용자마다 예측 불가능한 `sessionId`와 시작 시각을 발급한다.
- [x] 익명→등록, A→B와 A→익명에 새 session ID, 전환 이유와 변경 시각을 발행한다.
- [x] 이전·현재 session ID, 전환 이유, 단조 증가 sequence와 변경 시각을 가진 내부 변경
  event를 발행하고 구독 해제를 lifecycle에서 정리한다.
- [x] snapshot capture와 `is_current(sessionId)` 검증을 같은 session 상태 경계에서
  thread-safe하게 제공한다.
- [x] session service는 `DeskController`를 직접 호출하거나 mode를 소유하지 않고, task 06이
  목표 교체·MANUAL 보존·STOP 순서를 적용할 수 있는 불변 snapshot을 제공한다.
- [x] 서버 시작·재시작에서 session 없음으로 시작하고 fake fresh 관측을 요구한다.
- [ ] profile 또는 얼굴 삭제 시 활성 session과 후보를 원자적으로 무효화한다.
- [x] `/api/current-user`와 등록 상태를 read-only snapshot으로 제공한다.

## Task 05 구현 메모 (fake-driven)

- SQLite schema는 v4이며 `face_embeddings.vector`는 little-endian float32 BLOB이다. 일반 API와 로그에는 vector, crop, box, similarity를 노출하지 않는다.
- local SFace model이 provision되면 extractor는 YuNet landmark row를 `alignCrop`에만 전달하고,
  crop은 저장하지 않는다. 초기 cosine 0.363 및 관련 quality/margin 값은 calibration 후보이며
  실제 user camera site validation은 아직 완료되지 않았다. 표본은 configured interval보다 빠르게
  연속 capture하지 않는다.
- `FaceIdentityService`는 Vision의 fresh face observation만 소비하고 session change 구독 경계를 제공한다. Desk/WLED/Voice를 직접 호출하지 않으며, `AutomationService`와 Assistant runtime이 current-user session event를 구독하는 연결은 fake 기반 자동 테스트로 검증했다.
- Task 09에서 실제 Pi CPU 지연, model load, 카메라 조명/가림 품질 및 threshold·margin을 현장 검증하고 production extractor를 연결해야 한다.

## 얼굴 일시 누락의 안전 원칙

fresh 단일 재실이 이어지면 얼굴이 보이지 않아도 등록 session과 AUTO를 유지한다. 반면 상단의
`MULTIPLE`이나 관측 연속성 단절은 session을 유지하면서 AUTO와 개인화 Voice를
차단한다. 등록 session은 같은 얼굴 재확인, 익명 session은 단일 재실 3초 재안정화 뒤 AUTO
차단을 해제한다. 별도 얼굴 재확인 timeout은 v1에 추가하지 않는다.

## 제외 범위

- 결제·출입 통제용 생체 인증과 liveness/anti-spoof 보장
- 얼굴 원본·영상 저장과 원격 얼굴 등록
- 여러 책상이나 여러 사용자의 동시 tracking
- production camera·얼굴 모델을 이용한 자세 기반 실제 높이 제어

## 자동 검증

- 얼굴 없이도 단일 재실 안정화로 익명 session이 생성되며, 한 frame 얼굴 결과로 등록
  session이나 사용자 전환이 생기지 않는다.
- 같은 사용자의 연속 관측만 확정되고 다른 profile과 중복 등록이 거절된다.
- 얼굴 일시 누락, 고품질 미등록 얼굴 3초, 이탈, 다중 사용자와 A→B 교대의 session snapshot이
  결정표와 일치한다.
- 등록·재등록·삭제 도중 이전 identity와 session으로 자동 이동할 수 없다.
- server restart 후 profile 데이터는 남지만 현재 사용자와 후보는 비어 있다.
- session 변경 event는 중복·역순 소비에서도 이전 session을 다시 유효하게 만들지 않으며
  Voice가 이전 run·TTS·follow-up을 취소할 근거를 제공한다.
- model version·dimension 불일치 embedding을 비교에 사용하지 않는다.
- 한 enrollment가 3개 미만 표본으로 성공하지 않고, 5개를 초과해 저장하지 않는다.
- API가 얼굴 이미지·embedding vector를 반환하지 않는다.

## 실측과 보정

- 등록 사용자별로 거리, 조명, 고개 각도, 안경·모자 조건을 수집한다.
- false accept를 false reject보다 위험하게 보고 threshold와 margin을 보수적으로 정한다.
- 닮은 사용자, 화면·사진 노출과 주변 통행이 session 전이에 미치는 결과를 기록한다.
- 임계값은 코드 상수로 흩뜨리지 않고 설정과 검증 근거를 함께 남긴다.

## 완료 조건

- 얼굴 등록·조회·취소·재등록·삭제가 API와 storage에서 원자적으로 동작한다.
- 서버만이 얼굴·재실 근거로 현재 사용자를 결정하고 Dashboard 선택은 영향을 주지 않는다.
- 등록·익명 모든 사용자 변경에서 새 `sessionId`가 발급되고 stale session 명령을 구분한다.
- 불확실·미등록·다중·오래된 얼굴 결과가 profile 확정 상태로 남지 않는다.
