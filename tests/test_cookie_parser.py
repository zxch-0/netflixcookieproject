import json

import pytest

from cookie_parser import ParseError, parse_accounts


def test_parse_raw_header():
    accounts = parse_accounts("NetflixId=abc123; SecureNetflixId=def456")
    assert len(accounts) == 1
    assert accounts[0].cookies["NetflixId"] == "abc123"
    assert accounts[0].has_required


def test_parse_json_list():
    payload = json.dumps(
        [{"name": "NetflixId", "value": "a"}, {"name": "SecureNetflixId", "value": "b"}]
    )
    accounts = parse_accounts(payload)
    assert len(accounts) == 1
    assert accounts[0].cookies["SecureNetflixId"] == "b"


def test_parse_json_mapping():
    payload = json.dumps({"NetflixId": "a", "SecureNetflixId": "b"})
    accounts = parse_accounts(payload)
    assert accounts[0].cookies["NetflixId"] == "a"


def test_parse_netscape():
    block = (
        ".netflix.com\tTRUE\t/\tTRUE\t1794168611\tNetflixId\tvalue1\n"
        ".netflix.com\tTRUE\t/\tTRUE\t1794168611\tSecureNetflixId\tvalue2"
    )
    accounts = parse_accounts(block)
    assert len(accounts) == 1
    assert accounts[0].cookies["NetflixId"] == "value1"


def test_parse_multiple_accounts_with_separator():
    text = "NetflixId=a; SecureNetflixId=b\n---\nNetflixId=c; SecureNetflixId=d"
    accounts = parse_accounts(text)
    assert [account.cookies["NetflixId"] for account in accounts] == ["a", "c"]
    assert [account.index for account in accounts] == [0, 1]


def test_parse_multiple_accounts_blank_line():
    text = "NetflixId=a; SecureNetflixId=b\n\nNetflixId=c; SecureNetflixId=d"
    assert len(parse_accounts(text)) == 2


def test_deduplicates_accounts():
    text = "NetflixId=a; SecureNetflixId=b\n---\nNetflixId=a; SecureNetflixId=b"
    assert len(parse_accounts(text)) == 1


def test_skips_blocks_without_netflix_id():
    text = "Foo=bar\n---\nNetflixId=a; SecureNetflixId=b"
    accounts = parse_accounts(text)
    assert len(accounts) == 1


def test_empty_text_raises():
    with pytest.raises(ParseError):
        parse_accounts("   ")


def test_account_roundtrip():
    account = parse_accounts("NetflixId=a; SecureNetflixId=b")[0]
    restored = type(account).from_public(account.public(), 3)
    assert restored.index == 3
    assert restored.cookies == account.cookies
    assert restored.label == "Compte 4"
