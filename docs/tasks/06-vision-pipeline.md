# 06. Vision 파이프라인

## 목표

Task 05의 카메라별 `RtspFrameSource`가 제공하는 최신 프레임만 사용해
얼굴·자세·재실 상태를 만들고, 영상 지연이 Desk STOP과 FastAPI 응답을 막지 않게
한다.

## 선행 조건

- [카메라 발행과 최신 프레임](05-media-pipeline.md) 완료

## 작업 목록

- [ ] Task 05가 생성한 사용자·자세 `RtspFrameSource`를 Vision 입력으로 연결한다.
- [ ] `(image, captured_at)`의 시각으로 오래된 프레임을 새 관측에서 제외한다.
- [ ] `FramePreprocessor`를 카메라별 필요한 주기로 구현한다.
- [ ] 얼굴, 자세, 재실 detector를 각각 독립된 클래스로 구현한다.
- [ ] 무거운 추론은 executor에서 실행하고 model 인스턴스는 한 번만 로드한다.
- [ ] `VisionStateService`에서 신원·자세·재실 안정화와 신선도를 통합한다.
- [ ] React가 MediaMTX 영상을 재생하고 FastAPI에서 Vision JSON 상태를 읽게 한다.

## 테스트

- [ ] 최신 frame getter 대역으로 전처리와 detector를 장치 없이 검증한다.
- [ ] RTSP 단절 시 오래된 프레임을 새 관측으로 사용하지 않는지 확인한다.
- [ ] 추론 중 health API와 Desk STOP 응답 지연을 측정한다.
- [ ] 처리 속도가 입력 FPS보다 느려도 프레임 메모리가 증가하지 않는지 확인한다.

## 완료 조건

두 카메라의 최신 Vision 상태와 미리보기를 제공하고, 스트림·모델 오류 시 결과를
안전한 `UNKNOWN` 또는 오류 상태로 전환한다.
