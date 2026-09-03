"""
test_naturalness.py — ตรวจสอบว่าการต่อเสียงข้ามภาษา (mixed_language) มีรอยสะดุดจริงไหม

จำลอง logic เดียวกับ server.py /tts (mixed_language=True) เป๊ะ แล้ววัดค่าแอมพลิจูด
ตรงรอยต่อของแต่ละท่อน — ถ้ากระโดดแรง (ไม่ค่อยๆ ลดลงเป็นศูนย์ก่อนตัด) = มีรอยคลิกจริง
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from server import OmniVoiceEngine, SAMPLE_RATE
from text_utils import split_by_language

import soundfile as sf

print("[test] loading engine ...")
eng = OmniVoiceEngine()
eng.load()
print("[test] ready")

text = "สวัสดีครับ วันนี้เราจะมาพูดถึง Artificial Intelligence และ Machine Learning กันนะครับ"
segments = split_by_language(text)
print(f"\nแบ่งได้ {len(segments)} ท่อน:")
for seg, lang in segments:
    print(f"  [{lang}] {seg!r}")

# ใช้เสียงสต็อก voice_01 เป็น clone_prompt (เหมือนที่ /tts ใช้ voice_id จริง)
v = eng.voices["voice_01"]
prompt = eng.build_prompt(v["ref_audio"], v["meta"]["ref_text"])

wavs = []
for seg, lang in segments:
    w, dur = eng._run(seg, clone_prompt=prompt, language=lang, speed=1.4, num_step=24)
    wavs.append(w)
    print(f"  generated [{lang}] dur={dur:.2f}s peak={np.max(np.abs(w)):.3f} "
          f"first10ms_rms={np.sqrt(np.mean(w[:240]**2)):.4f} "
          f"last10ms_rms={np.sqrt(np.mean(w[-240:]**2)):.4f}")

wav_concat = np.concatenate(wavs) if len(wavs) > 1 else wavs[0]
sf.write("test_naturalness_concat.wav", wav_concat, SAMPLE_RATE)
print(f"\nsaved: test_naturalness_concat.wav (dur={len(wav_concat)/SAMPLE_RATE:.2f}s)")

# วิเคราะห์รอยต่อระหว่างแต่ละท่อน — ดูค่าแอมพลิจูดที่จุดตัดจริง (sample สุดท้ายของท่อนก่อน
# vs sample แรกของท่อนถัดไป) ถ้ากระโดดเกิน threshold = discontinuity ชัดเจน (จะได้ยินเป็นคลิก)
print("\n=== วิเคราะห์รอยต่อ ===")
cursor = 0
for i in range(len(wavs) - 1):
    cursor += len(wavs[i])
    end_of_prev = wavs[i][-1]
    start_of_next = wavs[i + 1][0]
    jump = abs(float(start_of_next) - float(end_of_prev))
    # เทียบกับค่าเฉลี่ยความดังของเสียง ถ้า jump ใหญ่กว่านี้มากถือว่าสะดุดชัดเจน
    typical_amp = float(np.mean(np.abs(wavs[i][-1000:])))
    print(f"รอยต่อ {i}->{i+1}: sample ก่อนตัด={end_of_prev:.4f}, sample หลังตัด={start_of_next:.4f}, "
          f"jump={jump:.4f}, typical_amp_ก่อนตัด={typical_amp:.4f}, "
          f"{'⚠️ สะดุดชัดเจน (jump >> typical_amp)' if jump > typical_amp * 3 else 'พอรับได้'}")
