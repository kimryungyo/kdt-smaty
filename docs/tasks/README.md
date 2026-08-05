# 구현 작업 목록

기본 골조 이후 작업을 2~3개월 안에 시연 가능한 순서로 정리한다. 번호순으로
진행하며, 각 문서의 완료 조건을 충족한 뒤 다음 작업으로 넘어간다.

새 작업을 추가하거나 기존 작업의 구조를 크게 바꾸기 전에는
[계획 및 설계 가이드](../guides/README.md)를 먼저 확인한다. 특히 단기 프로젝트
범위를 넘는 계층·프로세스·framework를 추가할 때는 필요 근거를 명시한다.

## 진행 순서

| 순서 | 작업 | 예상 기간 | 상태 |
| ---: | --- | ---: | --- |
| 01 | [MQTT 기반](01-mqtt-foundation.md) | 2~3일 | 다음 작업 |
| 02 | [책상 I/O 어댑터](02-desk-io.md) | 3~5일 | 대기 |
| 03 | [책상 제어](03-desk-control.md) | 5~7일 | 대기 |
| 04 | [대시보드와 프로필](04-dashboard-and-profiles.md) | 4~6일 | 대기 |
| 05 | [MediaMTX 영상 인프라](05-media-pipeline.md) | 3~4일 | 대기 |
| 06 | [Vision 파이프라인](06-vision-pipeline.md) | 8~12일 | 대기 |
| 07 | [자동화와 외부 장치](07-automation.md) | 5~7일 | 대기 |
| 08 | [통합·실물 검증](08-system-validation.md) | 4~5일 | 대기 |

예상 기간은 순수 구현일 기준이며 카메라·ESP32 실물 조정 시간을 포함하면 전체
8~11주를 예상한다. 장치 납기나 모델 학습이 늦어져도 01~04의 책상 수동 제어
시연은 독립적으로 완성할 수 있도록 순서를 분리했다.

## 공통 작업 규칙

- 현재 task 문서의 범위를 완료하기 전 다음 계층을 미리 일반화하지 않는다.
- 기능 객체는 `bootstrap.py`에서 생성하고 `AppContainer`가 프로세스당 하나만
  소유한다.
- FastAPI route와 event handler만 `get_*()`를 사용하고, 핵심 클래스 간 의존성은
  생성자로 전달한다.
- I/O는 async, 메모리 snapshot getter는 sync로 작성한다.
- MQTT 명령은 QoS 1, `retain=false`를 기본으로 해 broker에 마지막 명령을
  보관하지 않으며 기존 ESP32 계약을 먼저 보존한다.
- 실제 책상 이동은 가짜 어댑터 테스트와 STOP 검증을 통과한 뒤 제한된 범위에서만
  수행한다.
- 물리 카메라는 FFmpeg가 단독으로 열고 Python에는 MediaMTX 업로더를 만들지 않는다.
- 작업을 완료하면 해당 문서의 체크박스와 상태 표를 함께 갱신한다.

## 문서 관계

- 계획·설계 판단 기준: [계획 및 설계 가이드](../guides/README.md)
- 큰 단계와 일정: [구현 순서](../implementation/roadmap.md)
- 클래스 책임과 시그니처: [컴포넌트 설계](../architecture/component-design.md)
- async와 singleton: [실행과 동시성](../architecture/runtime-and-concurrency.md)
- STOP과 높이 안전: [책상 제어와 안전](../architecture/desk-safety.md)
