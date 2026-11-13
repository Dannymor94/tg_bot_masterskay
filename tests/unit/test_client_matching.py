import pytest
from pydantic import SecretStr

from app.domain.client_matching import Candidate, match_phone, normalize_phone


@pytest.mark.parametrize(
    "phone,expected",
    [
        ("+7 (900) 000-00-01", "+79000000001"),
        ("89000000001", "+79000000001"),
        ("79000000001", "+79000000001"),
        ("garbage", None),
        ("", None),
        ("+123", None),
    ],
)
def test_normalize(phone, expected):
    assert normalize_phone(phone) == expected


@pytest.mark.parametrize("ids,expected", [([], None), ([10], 10), ([10, 11], None), ([10, 10], 10)])
def test_match_zero_one_many(ids, expected):
    candidates = [Candidate(client_id=i, phone=SecretStr("+79000000001")) for i in ids]
    candidates.append(Candidate(client_id=99, phone=SecretStr("+79000000002")))
    assert match_phone("89000000001", candidates) == expected
    assert "+79000000001" not in repr(candidates)
