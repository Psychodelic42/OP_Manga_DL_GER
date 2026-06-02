import pytest

from app.downloader import clean_title, expand_chapter_spec, sanitize_filename


def test_expand_chapter_spec_single_range_and_list():
    assert expand_chapter_spec("1162") == [1162]
    assert expand_chapter_spec("1164-1162") == [1162, 1163, 1164]
    assert expand_chapter_spec("1150,1152-1154,1152") == [1150, 1152, 1153, 1154]


def test_expand_chapter_spec_rejects_invalid_input():
    with pytest.raises(ValueError):
        expand_chapter_spec("abc")


def test_clean_title_removes_page_suffix_and_unescapes_html():
    assert clean_title("Ein &amp; Zwei (Seite 1)") == "Ein & Zwei"


def test_sanitize_filename_replaces_forbidden_characters():
    assert sanitize_filename('One Piece: Kapitel/1?') == "One Piece_ Kapitel_1_"
