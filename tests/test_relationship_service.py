import pytest


@pytest.fixture
def rel_service(monkeypatch):
    monkeypatch.setenv("APPWRITE_ENDPOINT", "http://test/v1")
    monkeypatch.setenv("APPWRITE_PROJECT_ID", "test-proj")
    monkeypatch.setenv("APPWRITE_API_KEY", "test-key")
    monkeypatch.setenv("APPWRITE_DB_ID", "test-db")
    from app.services.relationship_service import RelationshipService

    return RelationshipService()


def test_familiarity_score_zero_baseline(rel_service):
    """全新對話的熟悉度必須夠低，否則 grooming 偵測規則無法觸發。

    2026-08-05 修改：原斷言為 `0.20 < score < 0.30`，那是在描述舊公式
    `0.40*msg + 0.40*days + 0.20*balance` 的地板分——balance_factor 在雙方平衡時
    恆為 1.0，等於無條件贈送 0.20。該地板使
    `abnormal_acceleration_grooming`（條件 `familiarity_max: 0.3`）永遠無法成立。
    公式改為 balance 乘法後地板消失，本測試改為守住「新對話必須遠低於 0.3」。
    """
    score = rel_service.compute_familiarity_score(0, 1, 0.5)

    assert score < 0.15, "全新對話的熟悉度過高會使 grooming 規則失效"


def test_familiarity_score_saturation(rel_service):
    score = rel_service.compute_familiarity_score(500, 90, 0.5)

    assert score > 0.95


def test_familiarity_score_imbalance_penalty(rel_service):
    balanced = rel_service.compute_familiarity_score(100, 30, 0.5)
    skewed = rel_service.compute_familiarity_score(100, 30, 0.9)

    assert balanced > skewed
    assert balanced - skewed >= 0.10


def test_familiarity_score_log_saturation_curve(rel_service):
    s500 = rel_service.compute_familiarity_score(500, 90, 0.5)
    s1000 = rel_service.compute_familiarity_score(1000, 90, 0.5)

    assert abs(s1000 - s500) < 0.05
