import pytest

from app.errors import AppError
from app.shortener.codes import ALPHABET, generate_code
from app.shortener.validation import assert_safe_url


def test_generate_code_is_base62() -> None:
    code = generate_code()
    assert len(code) == 7
    assert all(ch in ALPHABET for ch in code)


def test_https_example_allowed() -> None:
    assert_safe_url("https://example.com/path", allow_private=False)


def test_javascript_rejected() -> None:
    with pytest.raises(AppError) as exc:
        assert_safe_url("javascript:alert(1)", allow_private=True)
    assert exc.value.code == "url_unsafe"


def test_userinfo_rejected() -> None:
    with pytest.raises(AppError) as exc:
        assert_safe_url("https://user:pass@example.com/", allow_private=True)
    assert exc.value.code == "url_unsafe"


def test_localhost_rejected_when_private_disallowed() -> None:
    with pytest.raises(AppError) as exc:
        assert_safe_url("http://localhost/secret", allow_private=False)
    assert exc.value.code == "url_unsafe"


def test_localhost_allowed_when_private_ok() -> None:
    assert_safe_url("http://127.0.0.1/ok", allow_private=True)
