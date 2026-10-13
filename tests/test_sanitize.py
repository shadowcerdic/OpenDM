"""Unit tests for filename sanitization and Content-Disposition parsing."""
from opendm.sanitize import sanitize_filename, parse_content_disposition, unique_destination


def test_sanitize_traversal():
    assert sanitize_filename("../../x") == "x"
    assert sanitize_filename("../../../../etc/passwd") == "etcpasswd"
    assert sanitize_filename("..\\..\\boot.ini") == "boot.ini"
    assert sanitize_filename("%2e%2e%2fx") == "x"


def test_sanitize_persian_and_unicode():
    assert sanitize_filename("پیوست-مستند.pdf") == "پیوست-مستند.pdf"
    assert sanitize_filename("دانلود_فایل_شماره_۱.zip") == "دانلود_فایل_شماره_۱.zip"


def test_sanitize_query_and_fragment_chars():
    res = sanitize_filename("file?v=1&foo#bar.zip")
    assert "?" not in res and "#" not in res
    assert res.endswith(".zip")


def test_sanitize_windows_reserved():
    assert sanitize_filename("CON.txt") == "_CON.txt"
    assert sanitize_filename("con") == "_con"
    assert sanitize_filename("nul.tar.gz") == "_nul.tar.gz"
    assert sanitize_filename("aux") == "_aux"
    assert sanitize_filename("com1.bin") == "_com1.bin"


def test_sanitize_overly_long():
    long_name = "a" * 300 + ".tar.gz"
    res = sanitize_filename(long_name, max_len=100)
    assert len(res) <= 100
    assert res.endswith(".gz")


def test_content_disposition_rfc5987():
    cd = "attachment; filename*=UTF-8''%D9%BE%DB%8C%D9%88%D8%B3%D8%AA.pdf"
    assert parse_content_disposition(cd) == "پیوست.pdf"


def test_content_disposition_legacy():
    cd = 'attachment; filename="report_2026.xlsx"'
    assert parse_content_disposition(cd) == "report_2026.xlsx"


def test_unique_destination(tmp_path):
    f1 = tmp_path / "test.txt"
    f1.write_text("hello")
    f2 = unique_destination(tmp_path, "test.txt")
    assert f2.name == "test (1).txt"
    f2.write_text("hello 2")
    f3 = unique_destination(tmp_path, "test.txt")
    assert f3.name == "test (2).txt"
