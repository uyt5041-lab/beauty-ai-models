"""Optional serving monitoring must preserve default budget and retry guards."""
from uuid import uuid4
import pytest
from src.image_generation.provider_attempts import ProviderAttemptStore
from src.image_generation.errors import GatewayError


def test_explicit_monitoring_records_zero_and_rejects_duplicate(tmp_path):
    store = ProviderAttemptStore(tmp_path / 'attempts.sqlite')
    job, project = uuid4(), uuid4()
    store.begin(job, project, 'c' * 64, budget_limit_usd='0',
                reservation_usd='0', monitoring_only=True)
    assert store.get(job, project)['reservation_usd'] == '0.000000'
    with pytest.raises(GatewayError):
        store.begin(job, project, 'c' * 64, budget_limit_usd='0',
                    reservation_usd='0', monitoring_only=True)


def test_default_policy_still_enforces_budget(tmp_path):
    store = ProviderAttemptStore(tmp_path / 'attempts.sqlite')
    with pytest.raises(GatewayError, match='exhausted'):
        store.begin(uuid4(), uuid4(), 'c' * 64,
                    budget_limit_usd='0', reservation_usd='1')


@pytest.mark.parametrize('flag', [1, 'true', None])
def test_monitoring_flag_is_strict_bool(tmp_path, flag):
    store = ProviderAttemptStore(tmp_path / 'attempts.sqlite')
    with pytest.raises(ValueError, match='MONITORING_POLICY_INVALID'):
        store.begin(uuid4(), uuid4(), 'c' * 64, budget_limit_usd='0',
                    reservation_usd='0', monitoring_only=flag)
