"""Phase 18: rollback switches + rollout status — see
app/services/runtime_rollout.py.
"""

from app.core.config import Settings
from app.services.runtime_rollout import RolloutStage, all_systems_are_rollback_safe, current_rollout_status


def _settings(**overrides) -> Settings:
    return Settings(**overrides)


class TestCurrentRolloutStatus:
    def test_reports_all_eight_named_systems(self):
        statuses = current_rollout_status(_settings())
        names = {s.system_name for s in statuses}
        assert names == {
            "PromptCompiler", "ModelGateway", "Document runtimes", "OpenCode", "ACP",
            "Native premium adapters", "Local bridge", "Automatic routing",
        }

    def test_reflects_the_real_opencode_flag_value(self):
        disabled = current_rollout_status(_settings(OPENCODE_RUNTIME_ENABLED=False))
        enabled = current_rollout_status(_settings(OPENCODE_RUNTIME_ENABLED=True))
        assert next(s for s in disabled if s.system_name == "OpenCode").currently_enabled is False
        assert next(s for s in enabled if s.system_name == "OpenCode").currently_enabled is True

    def test_native_premium_adapters_is_not_started_reflecting_the_phase_12_blocker(self):
        statuses = current_rollout_status(_settings())
        native = next(s for s in statuses if s.system_name == "Native premium adapters")
        assert native.stage == RolloutStage.NOT_STARTED
        assert native.currently_enabled is False

    def test_no_system_is_past_offline_evaluation_by_default(self):
        statuses = current_rollout_status(_settings())
        advanced_stages = {
            RolloutStage.SELECTED_DEVELOPERS, RolloutStage.PILOT_PROJECT,
            RolloutStage.PERCENT_25, RolloutStage.PERCENT_50, RolloutStage.PERCENT_100,
        }
        assert all(s.stage not in advanced_stages for s in statuses)


class TestAllSystemsAreRollbackSafe:
    def test_true_for_the_real_default_settings(self):
        assert all_systems_are_rollback_safe(current_rollout_status(_settings())) is True
