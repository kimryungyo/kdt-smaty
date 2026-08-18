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

- model: `hi_smarty_ko_mixed_v0_2_0.onnx`
- 호출어: `하이 스마티`
- SHA-256: `29bbe36b71105d9e4e3de24dcc368e7e1c681bde931085939224ec3e3cfe37ec`
- model key: `hi_smarty_ko`
- threshold: validation에서 선택한 `0.13`
- 연속 판정: 실제 streaming 검증 전 임시값 `2`
- 원본 release: `/srv/wakeword/artifacts/releases/hi-smarty-ko-mixed-v0.2.0/`

학습에는 합성 TTS, Zeroth-Korean 일반 발화, MUSAN 환경음과 MIT IR, 실제 화자 4명의
녹음 130개를 사용했다. 실제 화자 시험은 학습 화자와 같은 녹음 세션에서 이뤄졌으므로,
대상 microphone의 연속 생활 소음과 미학습 화자에 대한 운영 품질을 보장하지 않는다.
frozen 평가에서 실제 화자 recall은 13/13, 고난도 합성 유사 발화 오탐은 1/250이었다.

provider 생성 TTS와 corpus attribution 및 재배포 조건은 원본 release의
`MODEL_CARD.md`와 provenance를 확인한다. 이 문서는 생성 TTS에 새 배포 license를
부여하지 않는다.
