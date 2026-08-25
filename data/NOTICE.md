# Third-party data

`en_th_transliteration.tsv` is copied from
[wannaphong/thai-english-transliteration-dictionary](https://github.com/wannaphong/thai-english-transliteration-dictionary)
(Apache License 2.0), used unmodified. Only rows with `check == "True"`
(verified against the Royal Society of Thailand's transliteration rules)
are loaded by `text_utils._load_bundled_dict`.
