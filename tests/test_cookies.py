import io
import zipfile

from app import main

NETSCAPE = ".netflix.com\tTRUE\t/\tTRUE\t0\tNetflixId\tAAA111\n.netflix.com\tTRUE\t/\tTRUE\t0\tSecureNetflixId\tBBB222\n"


def test_netscape_is_parsed():
    bundles = main.parse_cookies(NETSCAPE)
    assert len(bundles) == 1
    assert bundles[0]["NetflixId"] == "AAA111"
    assert bundles[0]["SecureNetflixId"] == "BBB222"


def test_json_is_parsed():
    payload = '[{"domain": ".netflix.com", "name": "NetflixId", "value": "JSON1"}]'
    assert main.parse_cookies(payload)[0]["NetflixId"] == "JSON1"


def test_raw_header_is_parsed():
    assert main.parse_cookies("NetflixId=RAW1; SecureNetflixId=RAW2")[0]["NetflixId"] == "RAW1"


def test_multiple_accounts_are_split():
    bundles = main.parse_cookies(NETSCAPE + ".netflix.com\tTRUE\t/\tTRUE\t0\tNetflixId\tAAA999\n")
    assert [bundle["NetflixId"] for bundle in bundles] == ["AAA111", "AAA999"]


def test_duplicates_are_dropped():
    assert len(main.merge_accounts([NETSCAPE, NETSCAPE])) == 1


def test_garbage_returns_nothing():
    assert main.parse_cookies("hello world") == []


def test_zip_expansion():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.txt", NETSCAPE)
        archive.writestr("b.txt", ".netflix.com\tTRUE\t/\tTRUE\t0\tNetflixId\tZZZ\n")
        archive.writestr("empty.txt", "nothing")

    texts, sources = [], []
    skipped, budget = main.expand_zip(buffer.getvalue(), texts, sources, 10_000_000)
    assert skipped == 1
    assert len(texts) == 2
    assert sources == ["a.txt", "b.txt"]
    assert budget < 10_000_000


def test_bad_zip():
    skipped, budget = main.expand_zip(b"not a zip", [], [], 100)
    assert skipped == 1
    assert budget == 100
