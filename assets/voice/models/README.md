# Wake Word model

`hi_smarty_ko_synthetic_v0_1_0.onnx`는 `하이 스마티` 호출어용
`livekit-wakeword==0.2.1` classifier다.

- model key: `hi_smarty_ko`
- SHA-256: `893c8d8d06892aac3f9b285a36801acb92f6a140c886e19ca1679822e55217c2`
- classifier input: float32 `(batch, 16, 96)` embeddings
- audio runtime input: 16kHz mono PCM16, rolling 32,000 samples (2 seconds)
- provisional threshold: `0.13`
- provisional consecutive evaluations: `2`
- source release: `/srv/wakeword/artifacts/releases/hi-smarty-ko-synthetic-v0.1.0/`

현재 모델은 실제 사용자 음성이 없는 합성 데이터 기준선이다. 운영 품질을 보장하지
않으며, 실제 장치의 연속 오디오로 임계값과 오탐률을 다시 검증해야 한다. 데이터 및
재배포 조건은 원본 release의 `MODEL_CARD.md`를 함께 확인한다.
