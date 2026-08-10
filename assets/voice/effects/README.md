# Voice effect assets

두 파일은 외부 음원을 사용하지 않고 이 프로젝트를 위해 Python 표준 라이브러리로
직접 합성했다. 음성 문장이 아닌 짧은 sine-wave chime이며 프로젝트 코드와 같은 조건으로
사용할 수 있다.

| 파일 | 용도 | 형식 | 길이 | SHA-256 |
| --- | --- | --- | --- | --- |
| `acknowledgement.wav` | Wake Word 확인 | 24kHz mono PCM16 WAV | 100ms | `bbf7efc0facaabf8873dcf6f877acb3470dc8f916874410fd5b80b5c7c8ee173` |
| `error.wav` | turn 오류 | 24kHz mono PCM16 WAV | 220ms | `48cf4934b68c33cb7c69bb9524477745f16d1c41d79bba10423071b6faa2e33c` |

생성 방식은 24kHz sample마다 sine 값을 계산하고, 시작 10ms와 끝 20ms에 linear fade를
적용한 뒤 Python `wave`로 PCM16 WAV를 기록하는 방식이다. acknowledgement는 880Hz와
1320Hz, error는 330Hz와 220Hz를 합성했으며 최대 진폭은 7,000이다.

재현 시 `scripts/`나 runtime generator를 추가하지 않고 아래와 같은 Python 표준
라이브러리 코드로 동일한 sample을 생성한다.

```python
import math
import struct
import wave


def make(path, duration, tones):
    rate = 24_000
    count = round(rate * duration)
    frames = []
    for index in range(count):
        elapsed = index / rate
        envelope = min(1.0, index / (rate * 0.01), (count - index - 1) / (rate * 0.02))
        value = sum(math.sin(2 * math.pi * tone * elapsed) for tone in tones) / len(tones)
        frames.append(struct.pack("<h", round(7_000 * max(0, envelope) * value)))
    with wave.open(path, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"".join(frames))
```
