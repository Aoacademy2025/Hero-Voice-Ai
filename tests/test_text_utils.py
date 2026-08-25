"""
test_text_utils.py — เทสของ text_utils.py (pure function, ไม่ต้องมี GPU/โมเดล)

เก็บเคสที่เจอปัญหาจริงระหว่างพัฒนาไว้ กันแก้โค้ดรอบหน้าแล้วกลับไปพังซ้ำ
(เช่น "Xiaomi" ที่ครั้งหนึ่งเคยทับศัพท์ผิดเป็น "ซีเอ็กซี่เอ็มไอวาย" ตอนพึ่ง LLM ล้วน)

รัน: python -m pytest tests/ -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from text_utils import (
    chunk_text,
    normalize_thai_numbers,
    split_by_language,
    split_sentences,
    transliterate_english,
)


# ── transliterate_english ──────────────────────────────────────────
class TestTransliterateEnglish:
    def test_common_tech_words_from_curated_dict(self):
        assert transliterate_english("Google") == "กูเกิล"
        assert transliterate_english("AI") == "เอไอ"
        assert transliterate_english("WiFi") == "ไวไฟ"

    def test_brand_names_from_curated_dict(self):
        # เคสจริงที่ Gemini/LLM เคยทับศัพท์ผิด (สะกดตัวอักษรแยก) — ต้องมาจากดิกคัดเอง
        assert transliterate_english("Xiaomi") == "เสี่ยวมี่"
        assert transliterate_english("Snapdragon") == "สแนปดรากอน"

    def test_words_from_bundled_external_dict(self):
        # มาจาก wannaphong/thai-english-transliteration-dictionary (ไม่ใช่ดิกคัดเอง)
        assert transliterate_english("YouTube") == "ยูทูบ"
        assert transliterate_english("subscribe") == "ซับสไกรบ์"

    def test_case_insensitive_lookup(self):
        assert transliterate_english("google") == "กูเกิล"
        assert transliterate_english("GOOGLE") == "กูเกิล"
        assert transliterate_english("GoOgLe") == "กูเกิล"

    def test_unknown_word_passes_through_unchanged(self):
        # คำที่ไม่มีในดิกไหนเลย และปิด Gemini fallback (ดีฟอลต์ปิด) — ต้องปล่อยผ่านเดิม
        assert transliterate_english("Qualcomm") == "Qualcomm"

    def test_words_from_unverified_tier(self):
        # มาจาก BUNDLED_EN_TO_THAI_UNVERIFIED (check != "True") — ชั้นสำรองสุดท้ายก่อน Gemini
        assert transliterate_english("bodyguard") != "bodyguard"
        assert transliterate_english("Xbox") == "เอ็กซ์บ็อกซ์"

    def test_multi_word_phrase_matching(self):
        # วลีหลายคำในดิก ต้องจับได้ทั้งวลี ไม่ใช่แค่คำเดี่ยว ๆ
        assert transliterate_english("Man U") == "แมนยู"
        assert transliterate_english("Santa Claus") == "ซานตาคลอส"
        out = transliterate_english("ผมชอบดู Man U เล่นบอล และสนใจเรื่อง deep learning มาก")
        assert "แมนยู" in out
        assert "ดีปเลิร์นนิง" in out
        assert "Man U" not in out

    def test_mixed_thai_english_sentence(self):
        out = transliterate_english("วันนี้เราจะใช้ AI ช่วย download ไฟล์จาก Google Drive")
        assert "เอไอ" in out
        assert "ดาวน์โหลด" in out
        assert "กูเกิล" in out
        assert "ไดรฟ์" in out
        assert "AI" not in out  # ต้องถูกแทนที่ ไม่ใช่แค่เติมข้าง ๆ

    def test_full_marketing_script_end_to_end(self):
        # เคสจริงจาก session ที่ใช้ตรวจผลลัพธ์ก่อนหน้านี้
        script = (
            "สินค้ารุ่น iPhone 15 Pro ใช้ชิป Snapdragon 8 Gen 3 "
            "รองรับ USB-C และมีโหมด Night Mode"
        )
        out = transliterate_english(script)
        assert "ไอโฟน" in out
        assert "เสี่ยวมี่" not in out  # ไม่มี Xiaomi ในเคสนี้ กันพลาดคัดลอกผิด
        assert "สแนปดรากอน" in out
        assert "เจน" in out
        assert "ยูเอสบีซี" in out
        assert "ไนท์" in out and "โหมด" in out


# ── normalize_thai_numbers ──────────────────────────────────────────
class TestNormalizeThaiNumbers:
    def test_plain_integer(self):
        assert normalize_thai_numbers("15") == "สิบห้า"
        assert normalize_thai_numbers("21") == "ยี่สิบเอ็ด"
        assert normalize_thai_numbers("100") == "หนึ่งร้อย"

    def test_number_with_thousands_separator(self):
        assert normalize_thai_numbers("1,250") == "หนึ่งพันสองร้อยห้าสิบ"

    def test_decimal_number(self):
        assert normalize_thai_numbers("32.5 องศา") == "สามสิบสอง จุด ห้า องศา"

    def test_currency_baht_no_double_word(self):
        out = normalize_thai_numbers("1,250 บาท")
        assert out == "หนึ่งพันสองร้อยห้าสิบบาทถ้วน"
        assert out.count("บาท") == 1  # กันบั๊ก "บาทถ้วน บาท" ซ้ำที่เคยเจอ

    def test_currency_with_satang(self):
        out = normalize_thai_numbers("39,900.50 บาท")
        assert "สตางค์" in out
        assert out.count("บาท") == 1

    def test_phone_number_reads_digit_by_digit(self):
        out = normalize_thai_numbers("โทร 081-234-5678")
        assert out == "โทร ศูนย์ แปด หนึ่ง สอง สาม สี่ ห้า หก เจ็ด แปด"

    def test_phone_number_not_read_as_cardinal(self):
        # ต้องไม่กลายเป็นจำนวนขนาดใหญ่ก้อนเดียว (เช่น "แปดสิบเอ็ดล้าน...")
        out = normalize_thai_numbers("081-234-5678")
        assert "ล้าน" not in out

    def test_full_script_with_price_phone_and_time(self):
        # เคสจริงจาก test_tts.py ("03_numbers_thai")
        out = normalize_thai_numbers(
            "ราคาทั้งหมด 1,250 บาท โทร 081-234-5678 เวลา 9 โมงครึ่ง"
        )
        assert "หนึ่งพันสองร้อยห้าสิบบาทถ้วน" in out
        assert "ศูนย์ แปด หนึ่ง สอง สาม สี่ ห้า หก เจ็ด แปด" in out
        assert "เก้า โมงครึ่ง" in out
        assert out.count("บาท") == 1

    def test_combined_with_transliteration(self):
        script = "สินค้ารุ่น iPhone 15 Pro ราคา 39,900 บาท ลด 20 เปอร์เซ็นต์"
        out = normalize_thai_numbers(transliterate_english(script))
        assert "ไอโฟน" in out
        assert "สิบห้า" in out  # "15" ก่อนคำว่า Pro
        assert "สามหมื่นเก้าพันเก้าร้อยบาทถ้วน" in out
        assert "ยี่สิบ" in out
        assert out.count("บาท") == 1


# ── split_by_language ────────────────────────────────────────────────
class TestSplitByLanguage:
    def test_pure_thai_is_single_segment(self):
        segs = split_by_language("สวัสดีครับ วันนี้อากาศดี")
        assert len(segs) == 1
        assert segs[0][1] == "Thai"

    def test_thai_english_mix_splits_into_segments(self):
        segs = split_by_language("ผมชอบ Google และ Facebook มาก")
        langs = [lang for _, lang in segs]
        assert "Thai" in langs
        assert "English" in langs

    def test_numbers_attach_to_previous_segment_not_split(self):
        segs = split_by_language("ราคา 15 บาท")
        # เลขเป็น neutral ต้องไม่ทำให้เกิดช่วงแยกใหม่
        assert len(segs) == 1
        assert segs[0][1] == "Thai"

    def test_lao_script_detected_separately(self):
        # ช่วง unicode ภาษาลาว (0E80-0EFF) ต้องถูกแยกเป็น "Lao" ไม่ใช่ "Thai"
        segs = split_by_language("ສະບາຍດີ")
        assert any(lang == "Lao" for _, lang in segs)

    def test_empty_string_returns_fallback(self):
        segs = split_by_language("")
        assert segs == [("", "Thai")]


# ── split_sentences / chunk_text ─────────────────────────────────────
class TestChunking:
    def test_split_sentences_only_on_punctuation_and_newline(self):
        # split_sentences ตัดตามเครื่องหมายจบประโยค/ขึ้นบรรทัดใหม่เท่านั้น
        # ช่องว่างเปล่า ๆ ไม่ตัด (นั่นเป็นหน้าที่ของ chunk_text/_hard_wrap แทน)
        sents = split_sentences("สวัสดีครับ วันนี้อากาศดี ยินดีต้อนรับ")
        assert len(sents) == 1

        sents = split_sentences("สวัสดีครับ! วันนี้อากาศดี\nยินดีต้อนรับ")
        assert len(sents) == 3

    def test_chunk_text_respects_max_chars(self):
        long_text = "คำ" * 300
        chunks = chunk_text(long_text, min_chars=60, max_chars=220)
        assert all(len(c) <= 220 for c in chunks)

    def test_chunk_text_merges_short_sentences(self):
        text = "หนึ่ง สอง สาม สี่ ห้า"
        chunks = chunk_text(text, min_chars=60, max_chars=220)
        # ประโยคสั้น ๆ ต้องถูกรวมเป็นก้อนเดียว ไม่ใช่แยกทีละคำ
        assert len(chunks) == 1

    def test_chunk_text_empty_input(self):
        assert chunk_text("") == []
