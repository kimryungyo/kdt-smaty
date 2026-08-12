# 05. 얼굴 식별과 사용자 세션

## 사용자 결과

사용자는 profile 설정에서 얼굴을 등록·재등록·삭제할 수 있다. 서버는 Dashboard 선택과
무관하게 background 얼굴 식별과 연속 재실 근거로 현재 사용자를 결정하고, 사용자 교대마다
새 `sessionId`를 발급한다.

얼굴 등록 성공은 현재 사용자 강제 선택이 아니다. 등록 후 일반 background 식별에서 같은
얼굴이 다시 안정적으로 확인돼야 새 사용자 session이 생긴다.

## 현재 상태

- profile CRUD와 user-camera 최신 frame 입력 기반은 있다.
- 얼굴 detector, 정렬·품질 검사, embedding model과 저장소는 없다.
- 등록 session API와 현재 사용자 read-only API가 없다.
- Dashboard에서 선택한 profile이 화면 사용자처럼 표시되지만 서버 사용자 상태는 없다.
- 현재 사용자 연속성, 얼굴 재검증과 A→B 교대 규칙은 task 01에서 확정해야 한다.

## 구성 경계

```text
user-camera latest frame
  → 얼굴 검출·정렬·품질 검사
  → FaceEmbeddingExtractor
  → FaceRecognizer raw candidate
  → IdentityStateService 안정화
  + Vision 재실·인원수
  → CurrentUserSessionService
```

- embedding model은 애플리케이션당 하나만 load하고 등록과 식별이 공유한다.
- model이 동시 호출을 지원하지 않으면 model 경계의 작은 lock으로 직렬화한다.
- 현재 사용자 session은 embedding 저장소나 Dashboard route가 아니라 별도 서버 상태가
  소유한다.
- profile repository는 얼굴을 보고 현재 사용자를 선택하지 않는다.

## 저장 설계

구현 전에 다음을 확정한다.

- profile당 대표 embedding 하나 또는 여러 표본을 저장할지
- model 이름·version, embedding dimension, normalization과 생성 시각 metadata
- model 변경 시 기존 embedding을 무효화·재등록하는 방법
- binary/blob 또는 숫자 직렬화 형식과 SQLite transaction 경계
- profile 삭제·얼굴 삭제·재등록의 원자성

얼굴 이미지와 crop은 기본 저장하지 않는다. embedding vector, raw similarity와 내부 threshold는
일반 Dashboard API 및 로그에 노출하지 않는다.

## 구현 단계

### 얼굴 추론 기반

- [ ] 얼굴 detector, landmark 정렬과 embedding model을 선정하고 의존성을 고정한다.
- [ ] 얼굴 크기, blur, 가림, 각도와 밝기의 품질 거절 기준을 정의한다.
- [ ] executor에서 embedding을 추출하고 model load·동시 호출·종료를 검증한다.
- [ ] 등록 embedding cache가 저장·삭제 후 원자적으로 갱신되게 한다.

### 얼굴 등록 session

- [ ] `WAITING_FACE → CAPTURING → PROCESSING → SUCCEEDED` 상태와 실패 코드를 구현한다.
- [ ] 한 명의 얼굴만 있을 때 서로 다른 시점의 품질 표본을 수집한다.
- [ ] 표본 간 일관성을 검사하고 다른 profile과 중복되는 얼굴을 거절한다.
- [ ] 시작·상태 조회·취소·얼굴 삭제 API와 동시 등록 `409`를 구현한다.
- [ ] 등록·재등록·삭제 시작 시 task 01 계약대로 STOP·자동화 차단·후보 초기화를 요청한다.
- [ ] 취소·실패·서버 종료에서 임시 표본과 background task를 정리한다.

### background 식별

- [ ] best match threshold와 best-second margin을 적용하는 open-set 비교를 구현한다.
- [ ] 한 frame 후보와 확정 identity를 분리하고 연속 확인·freshness를 적용한다.
- [ ] 미등록, 다중 얼굴, 낮은 품질과 model 오류를 서로 구분해 내부 상태로 기록한다.
- [ ] 새로운 등록이나 삭제가 진행 중이면 오래된 identity 결과를 발행하지 않는다.

### 현재 사용자 session

- [ ] task 01의 얼굴 누락·재검증·이탈·다중 사용자 전이를 구현한다.
- [ ] 새 확정 사용자마다 예측 불가능한 `sessionId`와 시작 시각을 발급한다.
- [ ] A session에서 B candidate를 바로 교체하지 않고 STOP·초기화·재확정 순서를 지킨다.
- [ ] 서버 시작·재시작에서 session 없음으로 시작하고 fresh 관측을 요구한다.
- [ ] profile 또는 얼굴 삭제 시 활성 session과 후보를 원자적으로 무효화한다.
- [ ] `/api/current-user`와 등록 상태를 read-only snapshot으로 제공한다.

## 얼굴 일시 누락의 안전 원칙

session 표시 유지, Voice 문맥 유지와 새로운 AUTO 이동 허용은 같은 결정이 아니다. 예를 들어
재검증 중 profile 이름을 계속 표시하더라도 카메라 간 동일 인물 귀속이 불확실하면 새 자동
목표는 차단할 수 있다. 구체적인 전이는 task 01 결정표를 그대로 구현하고 임의의 장시간
“마지막 사용자 캐시”를 추가하지 않는다.

## 제외 범위

- 결제·출입 통제용 생체 인증과 liveness/anti-spoof 보장
- 얼굴 원본·영상 저장과 원격 얼굴 등록
- 여러 책상이나 여러 사용자의 동시 tracking
- 자세 기반 실제 높이 제어와 Dashboard 전체 화면 개편

## 자동 검증

- 한 frame, 낮은 품질과 미등록 얼굴만으로 session이 생성되지 않는다.
- 같은 사용자의 연속 관측만 확정되고 다른 profile과 중복 등록이 거절된다.
- 얼굴 일시 누락, 재검증 만료, 이탈, 다중 사용자와 A→B 교대가 결정표와 일치한다.
- 등록·재등록·삭제 도중 이전 identity와 session으로 자동 이동할 수 없다.
- server restart 후 profile 데이터는 남지만 현재 사용자와 후보는 비어 있다.
- model version·dimension 불일치 embedding을 비교에 사용하지 않는다.
- API가 얼굴 이미지·embedding vector를 반환하지 않는다.

## 실측과 보정

- 등록 사용자별로 거리, 조명, 고개 각도, 안경·모자 조건을 수집한다.
- false accept를 false reject보다 위험하게 보고 threshold와 margin을 보수적으로 정한다.
- 닮은 사용자, 화면·사진 노출과 주변 통행이 session 전이에 미치는 결과를 기록한다.
- 임계값은 코드 상수로 흩뜨리지 않고 설정과 검증 근거를 함께 남긴다.

## 완료 조건

- 얼굴 등록·조회·취소·재등록·삭제가 API와 storage에서 원자적으로 동작한다.
- 서버만이 얼굴·재실 근거로 현재 사용자를 결정하고 Dashboard 선택은 영향을 주지 않는다.
- 모든 사용자 변경에서 새 `sessionId`가 발급되고 stale session 명령을 구분할 수 있다.
- 불확실·미등록·다중·오래된 얼굴 결과가 profile 확정 상태로 남지 않는다.
