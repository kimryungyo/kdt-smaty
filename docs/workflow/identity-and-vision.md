# 사용자 식별과 Vision 워크플로우

현재 사용자는 Dashboard 선택이 아니라 서버의 background 얼굴 식별로만 결정한다.
`CurrentUserSnapshot`은 Dashboard, 자동화와 Voice가 읽는 단일 서버 사용자 문맥이다.

## 시작과 background 처리

```text
CameraPublisher.start()
  → RtspFrameSource.start()
  → frame 전처리
  → PresenceDetector / FaceRecognizer / PostureDetector
  → VisionStateService
  → CurrentUserSnapshot
```

처리가 입력 FPS보다 느려도 과거 frame queue를 쌓지 않고 최신 frame 하나만 처리한다.
오래된 frame은 새 관측으로 사용하지 않으며 무거운 추론은 event loop 밖에서 수행한다.

## 현재 사용자 상태

| 상태 | profile ID | 의미 |
| --- | --- | --- |
| `RECOGNIZED` | 있음 | 등록 얼굴 한 명을 안정적으로 식별 |
| `UNREGISTERED` | 없음 | 얼굴은 있지만 등록 사용자와 불일치 |
| `MULTIPLE` | 없음 | 여러 명이 감지됨 |
| `UNKNOWN` | 없음 | 관측 부족·불안정·오래된 frame 또는 장치 오류 |
| `VACANT` | 없음 | 이탈이 안정화됨 |

최소 snapshot 필드는 다음과 같다.

| 필드 | 의미 |
| --- | --- |
| `profileId` | 얼굴로 확정된 profile 또는 `null` |
| `status` | 위 현재 사용자 상태 |
| `observedAt` | 마지막 안정 얼굴·재실 관측 시각 |
| `expiresAt` | 판정을 더 이상 신뢰하지 않을 시각 |

한 frame의 후보를 바로 현재 사용자로 만들지 않는다. 동일 얼굴의 연속 관측과 freshness를
통과해야 하며, 이탈이나 신원 상실 유예 시간이 끝나면 이전 profile ID를 반드시 비운다.

## 얼굴 식별

```text
최신 user-camera frame
  → freshness와 얼굴 수 검사
  → 얼굴 정렬·품질 검사
  → FaceEmbeddingExtractor(executor)
  → 등록 임베딩 비교
  → FaceRecognizer 후보
  → VisionStateService 안정화
  → CurrentUserSnapshot 교체
```

`FaceEmbeddingExtractor` model은 애플리케이션 시작 시 한 번 load하고 얼굴 등록과 식별이
같은 인스턴스를 사용한다. model이 동시 호출을 지원하지 않으면 작은 내부 lock으로
직렬화한다. 요청마다 model이나 RTSP reader를 만들지 않는다.

## 얼굴 등록

```text
Dashboard 등록 시작
  → profile 존재 확인
  → camera·frame freshness 확인
  → 동시 등록 충돌 확인
  → enrollment ID 반환

background enrollment
  → 한 명의 얼굴 확인
  → 서로 다른 시점의 품질 표본 수집
  → 임베딩 추출과 표본 일관성 검사
  → profile ID에 원자적으로 저장
```

등록 상태는 다음과 같다.

```text
IDLE → WAITING_FACE → CAPTURING → PROCESSING → SUCCEEDED
                  └──────────────→ CANCELLED / FAILED
```

snapshot에는 enrollment/profile ID, 상태, 필요·채택 표본 수, 시각과 `failureCode`만
노출한다. 얼굴 이미지와 임베딩 vector는 API로 반환하지 않는다.

등록 중에는 background 신원 결과 발행을 잠시 멈추고 재실·자세는 계속 판정한다. 실제
성능 문제가 확인되기 전에는 별도 우선순위 queue를 만들지 않는다.

등록 완료도 현재 사용자를 설정하지 않는다. 다음 background 식별에서 해당 얼굴이 안정적으로
확인돼야 `RECOGNIZED`가 된다.

## 재실과 자세

재실과 자세는 별도 축이다. `VACANT`는 자세가 아니다.

- 재실: `PRESENT`, `VACANT`, `UNKNOWN`
- 자세: `SITTING`, `STANDING`, `UNKNOWN`

현재 사용자의 신원, 재실과 자세가 모두 fresh해야 자동화 입력으로 사용한다. 사용자 변경,
이탈, frame 만료 또는 다중 사용자에서는 자세 후보와 유지 timer를 무효화한다.

## Voice 사용자 문맥

profile별 기억은 fresh한 `RECOGNIZED`에서만 `profile:<profile_id>`로 읽고 저장한다.
`UNREGISTERED`, `MULTIPLE`, `UNKNOWN`, `VACANT` 또는 얼굴 후보만 있는 경우에는 사용자
기억을 사용하지 않는다. Dashboard에서 편집한 profile을 Voice 사용자로 사용하지 않는다.
