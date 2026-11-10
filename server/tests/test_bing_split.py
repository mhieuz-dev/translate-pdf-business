import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from thongngu_bing import split_for_bing  # noqa: E402


def test_short_text_untouched():
    assert split_for_bing("Hello world.") == ["Hello world."]


def test_long_text_split_at_sentence_end_without_losing_words():
    sentences = [f"Sentence number {i} talks about simultaneous localization and mapping." for i in range(40)]
    text = " ".join(sentences)
    parts = split_for_bing(text)
    assert len(text) > 1000 and len(parts) >= 3
    assert all(len(p) <= 1000 for p in parts)
    assert all(p.endswith(".") for p in parts)
    assert " ".join(parts).split() == text.split()


def test_never_cuts_inside_style_tag_or_formula():
    chunk = "Plain words here. " * 30
    styled = "<style id='1'>" + "bold text that keeps going. " * 20 + "</style>"
    text = chunk + styled + " Then {v12} is a formula. " + chunk
    parts = split_for_bing(text)
    assert all(len(p) <= 1000 for p in parts)
    for p in parts:
        assert p.count("<style id='1'>") == p.count("</style>")
        assert "{v12" not in p or "{v12}" in p
    assert " ".join(parts).split() == text.split()


from thongngu_bing import split_heading_number  # noqa: E402


def test_heading_number_is_kept_at_the_front():
    # Bing dịch "4 Direct Preference Optimization" thành "... Trực tiếp 4": phải tách số ra rồi gắn lại đầu
    assert split_heading_number("4 Direct Preference Optimization") == ("4", "Direct Preference Optimization")
    assert split_heading_number("5.1 Your Language Model Is Secretly a Reward Model") == ("5.1", "Your Language Model Is Secretly a Reward Model")
    assert split_heading_number("A.2 Deriving the Gradient") == ("A.2", "Deriving the Gradient")
    assert split_heading_number("3. Method") == ("3.", "Method")


def test_ordinary_text_is_not_treated_as_heading():
    assert split_heading_number("4 out of 5 annotators preferred the response.") is None  # câu thường, có dấu chấm cuối
    assert split_heading_number("2020 was a big year") is None  # năm, không phải số mục
    assert split_heading_number("Direct Preference Optimization") is None
    long = "1 " + "word " * 40
    assert split_heading_number(long) is None  # quá dài để là đề mục
