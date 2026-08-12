# Voice third-party software와 model

AI 스피커 기능은 현재 비상업 개인 프로젝트 범위에서 아래 package와 프로젝트에서
학습한 model을 사용한다. 상업적 사용이나 재배포 범위가 생기면 학습 데이터와 TTS
provider 조건을 다시 검토한다.

## livekit-wakeword

- 설치 version: `livekit-wakeword==0.2.1`
- upstream: <https://github.com/livekit/livekit-wakeword>
- pinned upstream commit: `1ec7f680df30ff4ca0ebae6b5983441e94b10980`
- runtime wheel SHA-256: `9576e103b6777619342ef9cce1d47727c39f1d6b7b564238f3467ace4d10a99a`
- package code license: Apache License 2.0
- 추론 framework: ONNX Runtime CPU provider
- 확인 환경: x86_64, Linux, Python 3.11.15와 3.12.13

package에 포함된 mel spectrogram과 speech embedding ONNX를 사용해 16kHz mono PCM16
2초 창을 `(16, 96)` embedding으로 변환하고, 프로젝트 classifier를 실행한다. 외부에서
model을 다운로드하지 않으므로 설치와 추론은 배포된 wheel과 repository model만으로
동작한다.

## hi_smarty_ko model

- model: `hi_smarty_ko_synthetic_v0_1_0.onnx`
- 호출어: `하이 스마티`
- SHA-256: `893c8d8d06892aac3f9b285a36801acb92f6a140c886e19ca1679822e55217c2`
- model key: `hi_smarty_ko`
- threshold: validation에서 선택한 `0.13`
- 연속 판정: 실제 streaming 검증 전 임시값 `2`
- 원본 release: `/srv/wakeword/artifacts/releases/hi-smarty-ko-synthetic-v0.1.0/`

학습에는 합성 TTS, Zeroth-Korean 일반 발화, MUSAN 환경음과 MIT IR을 사용했다. 실제
사용자나 대상 microphone 녹음은 포함하지 않은 비운영 합성 기준선이다. frozen test에서
positive recall 96.8%, 고난도 유사 발화 오탐 1/250이었지만, 연속 생활 소음에서 측정한
운영 오탐률은 아니다.

provider 생성 TTS와 corpus attribution 및 재배포 조건은 원본 release의
`MODEL_CARD.md`와 provenance를 확인한다. 이 문서는 생성 TTS에 새 배포 license를
부여하지 않는다.
