"""Saved-cost publication, exercised only with local fake W&B objects."""
import builtins
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from beauty_ai import cost_wandb as bridge


@pytest.fixture
def report_file(tmp_path):
    baseline = dict(total_cost_usd="2.000000000", known_partial_usd="2.000000000", accepted_ads=4,
                    unit_cost_usd="0.500000000", cost_item_count=4, known_cost_count=4, missing_cost_count=0,
                    missing_batch_count=0)
    candidate = dict(total_cost_usd="1.500000000", known_partial_usd="1.500000000", accepted_ads=5,
                     unit_cost_usd="0.300000000", cost_item_count=5, known_cost_count=5, missing_cost_count=0,
                     missing_batch_count=0)
    estimated = dict(baseline=baseline, candidate=candidate, unit_cost_reduction_pct="40.000000000",
                     counterfactual_difference_usd="1.000000000", reason_codes=[])
    actual = deepcopy(estimated)
    for arm in ("baseline", "candidate"):
        actual[arm].update(total_cost_usd=None, known_partial_usd="0.000000000", unit_cost_usd=None,
                           known_cost_count=0, missing_cost_count=actual[arm]["cost_item_count"])
    actual.update(unit_cost_reduction_pct=None, counterfactual_difference_usd=None,
                  reason_codes=["ACTUAL_COST_MISSING"])
    payload = dict(schema_version="beautyai-cost-report-v1", contract_version="beautyai-finops-v1",
                   report_id="a" * 64, mode="COMPARISON", data_kind="SYNTHETIC_FIXTURE",
                   inputs=dict(dataset_sha256="b" * 64, comparison_sha256="c" * 64),
                   scope={}, sources=[], overview={}, comparison=dict(estimated=estimated, actual=actual))
    path = tmp_path / "report.json"
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


class FakeArtifact:
    def __init__(self, name, type, metadata):
        self.name, self.type, self.metadata = name, type, deepcopy(metadata)
        self.files = {}

    def add_file(self, path, *, name):
        self.files[name] = Path(path).read_bytes()

    def download(self, *, root):
        target = Path(root)
        target.mkdir(parents=True, exist_ok=True)
        for name, data in self.files.items():
            (target / name).write_bytes(data)
        return str(target)


class FakeRun:
    def __init__(self, sdk, **kwargs):
        self.sdk = sdk
        self.id, self.config = kwargs["id"], deepcopy(kwargs["config"])
        self.summary, self.artifacts = {}, []
        self.state = "running"

    def log_artifact(self, artifact):
        self.sdk.fail("log_artifact")
        self.artifacts.append(artifact)

    def log(self, values):
        self.sdk.fail("log")
        for key, table in values.items():
            self.artifacts.append(SimpleNamespace(
                type="run_table", name=f"run-{self.id}-{key}", key=key,
                get=lambda requested, key=key, table=table: table if requested == key else None))

    def finish(self, exit_code=0):
        self.sdk.fail("finish")
        if self.sdk.drop_null_summaries:
            self.summary = {key: value for key, value in self.summary.items() if value is not None}
        self.state = "finished" if exit_code == 0 else "failed"

    def logged_artifacts(self):
        return list(self.artifacts)


class FakeAPI:
    def __init__(self):
        self.remote = {}
        self.calls = []
        self.failure = None
        self.access = "PRIVATE"
        self._service_api = SimpleNamespace(execute_graphql=self.query)

    def fail(self, operation):
        if self.failure == operation:
            raise PermissionError("private credential sk-" + "x" * 40)

    def query(self, query, variables):
        self.calls.append("privacy")
        self.fail("privacy")
        assert query.startswith("query")
        return {"project": {"name": variables["name"], "access": self.access}}

    def runs(self, path, *, per_page):
        self.calls.append("list")
        self.fail("list")
        yield from self.remote.values()
        self.fail("page")

    def run(self, path):
        self.calls.append("readback")
        self.fail("readback")
        return self.remote[path.rsplit("/", 1)[-1]]

    def flush(self):
        self.fail("flush")


class FakeSDK:
    def __init__(self, api):
        self.api = api
        self.initializations, self.settings = [], []
        self.failure = None
        self.drop_null_summaries = False

    def fail(self, operation):
        if self.failure == operation:
            raise RuntimeError("private credential sk-" + "x" * 40)

    def Settings(self, **kwargs):
        self.settings.append(kwargs)
        return kwargs

    def init(self, **kwargs):
        self.fail("init")
        self.initializations.append(kwargs)
        assert kwargs["id"] not in self.api.remote
        run = FakeRun(self, **kwargs)
        self.api.remote[run.id] = run
        return run

    Artifact = FakeArtifact

    @staticmethod
    def Table(*, columns, data):
        return SimpleNamespace(columns=deepcopy(columns), data=deepcopy(data))


@pytest.fixture
def remote():
    api = FakeAPI()
    return api, FakeSDK(api)


def sync(path, remote, **kwargs):
    api, sdk = remote
    return bridge.sync_cost_report(path, entity="example-team", project="costs", api=api, sdk=sdk,
                                   publish=True, **kwargs)


def remote_parts(remote):
    run = next(iter(remote[0].remote.values()))
    artifact = next(item for item in run.artifacts if item.type == "cost-comparison")
    table = next(item for item in run.artifacts if item.type == "run_table"
                 and item.key == "comparison").get("comparison")
    return run, artifact, table


def test_default_preparation_has_no_auth_network_sdk_or_image_imports(report_file, monkeypatch):
    class Forbidden:
        def __getattr__(self, key):
            pytest.fail("offline preparation touched SDK/API")

    original_import = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name == "wandb" or name.startswith("wandb."):
            pytest.fail("offline preparation imported authentication SDK")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    before = report_file.read_bytes()
    receipt = bridge.sync_cost_report(report_file, entity="team", project="costs", sdk=Forbidden(), api=Forbidden())
    assert receipt["status"] == "PREPARED" and receipt["readback_verified"] is False
    assert receipt["new_image_model_calls"] == 0
    assert receipt["report_file_sha256"] == hashlib.sha256(before).hexdigest()
    assert report_file.read_bytes() == before
    assert not (report_file.parent / "cost-wandb" / ("a" * 64) / "verified-receipt.json").exists()


def test_module_import_has_no_executor_or_model_dependencies():
    result = subprocess.run([sys.executable, "-c", "import sys; import beauty_ai.cost_wandb; "
        "assert not any(k in sys.modules for k in "
        "['wandb','beauty_ai.api_image_processor','beauty_ai.openai_images',"
        "'beauty_ai.experiment_runner','beauty_ai.inference_runtime','torch','PIL.Image'])"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_two_syncs_init_once_preserve_exact_file_and_verify_numeric_table(report_file, remote):
    original = report_file.read_bytes()
    first = sync(report_file, remote)
    assert first["status"] == "READBACK_VERIFIED" and first["deduplicated"] is False
    second = sync(report_file, remote)
    assert second["status"] == "READBACK_VERIFIED" and second["deduplicated"] is True
    api, sdk = remote
    assert len(sdk.initializations) == 1
    assert api.calls.count("list") == api.calls.count("readback") == 2
    run, artifact, table = remote_parts(remote)
    assert set(artifact.files) == {"report.json"}
    assert artifact.files["report.json"] == original == report_file.read_bytes()
    assert run.id == "compare-" + "a" * 20
    assert run.config["report_id"] == "a" * 64
    assert run.summary["report_file_sha256"] == hashlib.sha256(original).hexdigest()
    assert run.summary["estimated_unit_cost_reduction_pct"] == 40.0
    assert run.summary["estimated_counterfactual_difference_usd"] == 1.0
    assert run.summary["actual_counterfactual_difference_usd"] is None
    assert table.data[0][bridge.TABLE_COLUMNS.index("baseline_unit_cost_usd")] == 0.5
    assert table.data[1][bridge.TABLE_COLUMNS.index("baseline_unit_cost_usd")] is None
    assert sdk.initializations[0]["resume"] == "never"
    assert sdk.settings == [dict(console="off", disable_code=True, disable_git=True, save_code=False,
                                 x_disable_meta=True, x_disable_stats=True, silent=True,
                                 init_timeout=60, finish_timeout=120, finish_timeout_raises=True)]


@pytest.mark.parametrize("change,code", [
    ("file", "REMOTE_FILE"), ("config", "REMOTE_CONFIG"), ("identity", "REMOTE_SUMMARY"),
    ("summary", "REMOTE_SUMMARY"), ("null_summary", "REMOTE_SUMMARY"), ("table", "REMOTE_TABLE"),
    ("table_bool", "REMOTE_TABLE"), ("columns", "REMOTE_TABLE"), ("table_rows", "REMOTE_TABLE"),
    ("artifact", "REMOTE_ARTIFACT"), ("extra_artifact", "REMOTE_ARTIFACT"), ("unfinished", "RUN_UNFINISHED"),
])
def test_remote_inconsistency_never_overwrites_and_preserves_verified_receipt(report_file, remote, change, code):
    assert sync(report_file, remote)["status"] == "READBACK_VERIFIED"
    verified = report_file.parent / "cost-wandb" / ("a" * 64) / "verified-receipt.json"
    original_verified = verified.read_bytes()
    run, artifact, table = remote_parts(remote)
    if change == "file":
        artifact.files["report.json"] += b" "
    elif change == "config":
        run.config["report_id"] = "d" * 64
    elif change == "identity":
        run.summary["report_file_sha256"] = "d" * 64
    elif change == "summary":
        run.summary["estimated_unit_cost_reduction_pct"] = 99
    elif change == "null_summary":
        del run.summary["estimated_unit_cost_reduction_pct"]
    elif change == "table":
        table.data[0][bridge.TABLE_COLUMNS.index("unit_cost_reduction_pct")] = 99
    elif change == "table_bool":
        table.data[0][bridge.TABLE_COLUMNS.index("candidate_missing_cost_count")] = False
    elif change == "columns":
        table.columns.reverse()
    elif change == "table_rows":
        table.data.append(table.data[0])
    elif change == "artifact":
        artifact.metadata["report_id"] = "d" * 64
    elif change == "extra_artifact":
        run.artifacts.append(artifact)
    elif change == "unfinished":
        run.state = "running"
    receipt = sync(report_file, remote)
    assert receipt["status"] == "INCOMPLETE" and receipt["readback_verified"] is False
    assert code in receipt["error_code"]
    assert len(remote[1].initializations) == 1
    assert receipt["previous_verified_receipt"]["status"] == "READBACK_VERIFIED"
    assert verified.read_bytes() == original_verified
    # A further failure must retain the earlier verification evidence too.
    assert sync(report_file, remote)["previous_verified_receipt"] == receipt["previous_verified_receipt"]


@pytest.mark.parametrize("failure", ["privacy", "list", "page"])
def test_inaccessible_remote_is_not_confirmed_absent(report_file, remote, failure):
    api, sdk = remote
    api.failure = failure
    receipt = sync(report_file, remote)
    assert receipt["status"] == "INCOMPLETE"
    assert receipt["error_code"] == "WANDB_COST_SYNC_FAILED"
    assert not sdk.initializations
    assert "sk-" not in json.dumps(receipt)


@pytest.mark.parametrize("failure", ["readback", "flush"])
def test_unverified_upload_is_incomplete_and_later_retry_does_not_reupload(report_file, remote, failure):
    api, sdk = remote
    api.failure = failure
    receipt = sync(report_file, remote)
    assert receipt["status"] == "INCOMPLETE" and not receipt["readback_verified"]
    assert len(sdk.initializations) == 1
    api.failure = None
    assert sync(report_file, remote)["status"] == "READBACK_VERIFIED"
    assert len(sdk.initializations) == 1


@pytest.mark.parametrize("failure", ["log_artifact", "log", "finish"])
def test_unfinished_upload_is_never_reinitialized(report_file, remote, failure):
    api, sdk = remote
    sdk.failure = failure
    receipt = sync(report_file, remote)
    assert receipt["status"] == "INCOMPLETE" and "sk-" not in json.dumps(receipt)
    sdk.failure = None
    receipt = sync(report_file, remote)
    assert receipt["status"] == "INCOMPLETE"
    assert receipt["error_code"] == "WANDB_COST_REMOTE_RUN_UNFINISHED"
    assert len(sdk.initializations) == 1


@pytest.mark.parametrize("access", ["PUBLIC", "RESTRICTED", None])
def test_private_project_required_without_changing_existing_project(report_file, remote, access):
    api, sdk = remote
    api.access = access
    receipt = sync(report_file, remote)
    assert receipt["status"] == "INCOMPLETE"
    assert receipt["error_code"] == "WANDB_COST_PROJECT_PRIVACY_UNVERIFIED"
    assert api.calls == ["privacy"] and not sdk.initializations


def test_existing_run_requires_no_sdk_and_never_calls_init(report_file, remote):
    assert sync(report_file, remote)["status"] == "READBACK_VERIFIED"
    receipt = bridge.sync_cost_report(report_file, entity="example-team", project="costs", api=remote[0], publish=True)
    assert receipt["status"] == "READBACK_VERIFIED"
    assert len(remote[1].initializations) == 1


def test_source_change_during_remote_check_stops_before_init(report_file, remote):
    original = remote[0].query
    def query(*args):
        result = original(*args)
        report_file.write_bytes(report_file.read_bytes() + b" ")
        return result
    remote[0]._service_api.execute_graphql = query
    receipt = sync(report_file, remote)
    assert receipt["error_code"] == "WANDB_COST_STAGED_REPORT_MISMATCH"
    assert not remote[1].initializations


def test_exact_byte_change_with_same_report_identity_cannot_replace_staged_or_remote(report_file, remote):
    assert sync(report_file, remote)["status"] == "READBACK_VERIFIED"
    staged = report_file.parent / "cost-wandb" / ("a" * 64) / "report.json"
    original = staged.read_bytes()
    report_file.write_bytes(original + b" ")
    receipt = sync(report_file, remote)
    assert receipt["error_code"] == "WANDB_COST_STAGED_REPORT_MISMATCH"
    assert staged.read_bytes() == original
    assert len(remote[1].initializations) == 1


@pytest.mark.parametrize("field,value", [
    ("schema_version", "other"), ("report_id", "bad"), ("report_id", "../bad"),
    ("contract_version", "other"), ("mode", "other"), ("api_key", "hidden"),
    ("unexpected", "sk-" + "x" * 40),
])
def test_invalid_or_private_report_rejected_before_auth(report_file, remote, field, value):
    payload = json.loads(report_file.read_bytes())
    payload[field] = value
    report_file.write_text(json.dumps(payload))
    receipt = sync(report_file, remote)
    assert receipt["status"] == "INCOMPLETE"
    assert not remote[0].calls and not remote[1].initializations
    assert "hidden" not in json.dumps(receipt) and "sk-" not in json.dumps(receipt)


@pytest.mark.parametrize("raw", [b'{"schema_version":1,"schema_version":2}', b'[]', b'{"x":NaN}', b'not json'])
def test_malformed_json_is_safely_rejected(report_file, remote, raw):
    report_file.write_bytes(raw)
    receipt = sync(report_file, remote)
    assert receipt["error_code"] == "WANDB_COST_REPORT_INVALID"
    assert not remote[0].calls


@pytest.mark.parametrize("kind", ["source", "stage", "receipt"])
def test_symlinks_are_rejected_without_writing_outside(report_file, remote, tmp_path, kind):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "data.json"
    target.write_bytes(report_file.read_bytes())
    original = target.read_bytes()
    if kind == "source":
        report_file.unlink()
        report_file.symlink_to(target)
    elif kind == "stage":
        (tmp_path / "cost-wandb").symlink_to(outside, target_is_directory=True)
    else:
        (tmp_path / "cost-wandb-sync.json").symlink_to(target)
    receipt = sync(report_file, remote)
    assert receipt["status"] == "INCOMPLETE"
    assert target.read_bytes() == original and not remote[0].calls


def test_overview_preserves_unknown_denominators_and_headlines(report_file, remote):
    payload = json.loads(report_file.read_bytes())
    payload["mode"] = "OVERVIEW"
    payload["inputs"]["comparison_sha256"] = None
    for basis in ("estimated", "actual"):
        section = payload["comparison"][basis]
        for arm in ("baseline", "candidate"):
            section[arm] = {key: None for key in section[arm]}
        section.update(unit_cost_reduction_pct=None, counterfactual_difference_usd=None,
                       reason_codes=["COMPARISON_NOT_PROVIDED"])
    report_file.write_text(json.dumps(payload))
    assert sync(report_file, remote)["status"] == "READBACK_VERIFIED"
    run, _, _ = remote_parts(remote)
    assert run.summary["estimated_baseline_accepted_ads"] is None
    assert run.summary["actual_counterfactual_difference_usd"] is None


def test_actual_report_builder_overview_is_publishable(tmp_path, remote):
    from beauty_ai.cost_reporting import build_report
    report = build_report(dict(schema_version="beautyai-cost-dataset-v1", cost_items=[], batches=[], acceptances=[]))
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    receipt = sync(path, remote)
    assert receipt["status"] == "READBACK_VERIFIED"
    run, artifact, _ = remote_parts(remote)
    assert run.config["report_id"] == report["report_id"]
    assert json.loads(artifact.files["report.json"]) == report
    assert run.summary["estimated_unit_cost_reduction_pct"] is None


def test_actual_report_builder_comparison_is_publishable(tmp_path, remote):
    from beauty_ai.cost_reporting import build_report
    from test_cost_savings import fixture
    dataset, manifest = fixture()
    report = build_report(dataset, manifest)
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    assert sync(path, remote)["status"] == "READBACK_VERIFIED"
    run, artifact, _ = remote_parts(remote)
    assert run.summary["actual_unit_cost_reduction_pct"] == 40.0
    assert run.summary["estimated_counterfactual_difference_usd"] == 1.0
    assert run.summary["estimated_baseline_missing_batch_count"] == 0
    assert json.loads(artifact.files["report.json"]) == report


def test_report_id_prefix_collision_cannot_replace_existing_run(report_file, remote):
    assert sync(report_file, remote)["status"] == "READBACK_VERIFIED"
    payload = json.loads(report_file.read_bytes())
    payload["report_id"] = "a" * 20 + "b" * 44
    report_file.write_text(json.dumps(payload))
    receipt = sync(report_file, remote)
    assert receipt["error_code"] == "WANDB_COST_REMOTE_CONFIG_MISMATCH"
    assert len(remote[1].initializations) == 1


def test_uploaded_receipt_is_written_before_remote_readback(report_file, remote):
    api = remote[0]
    original = api.run
    observed = []
    def readback(path):
        observed.append(json.loads((report_file.parent / "cost-wandb-sync.json").read_bytes())["status"])
        return original(path)
    api.run = readback
    assert sync(report_file, remote)["status"] == "READBACK_VERIFIED"
    assert observed == ["UPLOADED"]


def test_staging_mutation_at_init_is_rejected_before_upload(report_file, remote):
    sdk = remote[1]
    original = sdk.init
    def initialize(**kwargs):
        run = original(**kwargs)
        (Path(kwargs["dir"]) / "report.json").write_bytes(b"changed")
        return run
    sdk.init = initialize
    receipt = sync(report_file, remote)
    assert receipt["error_code"] == "WANDB_COST_STAGED_REPORT_MISMATCH"
    assert not next(iter(remote[0].remote.values())).artifacts


def test_remote_report_download_cannot_escape_explicit_output(report_file, remote, tmp_path):
    assert sync(report_file, remote)["status"] == "READBACK_VERIFIED"
    _, artifact, _ = remote_parts(remote)
    artifact.download = lambda **kwargs: str(tmp_path)
    receipt = sync(report_file, remote)
    assert receipt["error_code"] == "WANDB_COST_REMOTE_DOWNLOAD_PATH_INVALID"
    assert len(remote[1].initializations) == 1


def test_verified_receipt_write_failure_cannot_leave_completed_main_receipt(report_file, remote, monkeypatch):
    original = bridge._write
    def write(path, data):
        if path.name == "verified-receipt.json":
            raise OSError("private disk error")
        original(path, data)
    monkeypatch.setattr(bridge, "_write", write)
    receipt = sync(report_file, remote)
    assert receipt["error_code"] == "WANDB_COST_RECEIPT_WRITE_FAILED"
    saved = json.loads((report_file.parent / "cost-wandb-sync.json").read_bytes())
    assert saved == receipt and saved["status"] == "INCOMPLETE"


@pytest.mark.parametrize("kwargs,code", [({"entity": "../team"}, "DESTINATION"),
    ({"project": "a?b"}, "DESTINATION"), ({"publish": "yes"}, "PUBLISH_FLAG")])
def test_argument_errors_have_fixed_safe_codes(report_file, kwargs, code):
    arguments = dict(entity="team", project="costs") | kwargs
    with pytest.raises(ValueError, match=code):
        bridge.sync_cost_report(report_file, **arguments)


@pytest.fixture
def generation_report_file(tmp_path):
    from beauty_ai.cost_reporting import build_report
    from test_generation_costs import fixture
    dataset, manifest = fixture(10, baseline="0.041213", candidate="0.0407115", unpaired=6)
    report = build_report(dataset, generation_comparison=manifest)
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    return path


def generation_table(remote):
    run = next(iter(remote[0].remote.values()))
    artifact = next(item for item in run.artifacts if item.type == "run_table"
                    and item.key == "generation_cost_estimate")
    return artifact, artifact.get("generation_cost_estimate")


def test_generation_builder_report_publishes_both_tables_exact_artifact_and_twenty_summary_values(
        generation_report_file, remote):
    original = generation_report_file.read_bytes()
    first = sync(generation_report_file, remote)
    assert first["status"] == "READBACK_VERIFIED" and not first["deduplicated"]
    second = sync(generation_report_file, remote)
    assert second["status"] == "READBACK_VERIFIED" and second["deduplicated"]
    assert len(remote[1].initializations) == 1
    run, artifact, comparison = remote_parts(remote)
    generation_artifact, table = generation_table(remote)
    assert {item.key for item in run.artifacts if item.type == "run_table"} == {
        "comparison", "generation_cost_estimate"}
    assert generation_artifact.name == f"run-{run.id}-generation_cost_estimate"
    assert generation_artifact.get("comparison") is None
    assert comparison.columns == bridge.TABLE_COLUMNS and len(comparison.data) == 2
    assert table.columns == [
        "comparison_id", "estimate_rate_version", "baseline_generation_count", "candidate_generation_count",
        "baseline_total_estimated_cost_usd", "candidate_total_estimated_cost_usd",
        "baseline_estimated_cost_per_generation_usd", "candidate_estimated_cost_per_generation_usd",
        "estimated_cost_reduction_pct", "estimated_cost_difference_usd", "per_generation_difference_usd",
        "input_record_count", "planned_pair_count", "compared_pair_count", "compared_record_count",
        "excluded_pair_count", "excluded_record_count", "coverage_pct", "assurance", "reason_codes",
    ]
    assert table.data == [[
        "comparison-1", "rate-1", 10, 10, 0.41213, 0.407115, 0.041213, 0.0407115,
        1.216849052, 0.005015, 0.0005015, 26, 10, 10, 20, 0, 6, 76.923076923,
        "OBSERVATIONAL_NOT_QUALITY_CONTROLLED", "UNPAIRED_RECORD",
    ]]
    for key, value in zip(table.columns, table.data[0], strict=True):
        assert run.summary[f"generation_{key}"] == value
    assert artifact.files["report.json"] == original == generation_report_file.read_bytes()
    report = json.loads(original)
    assert report["generation_comparison"]["estimated_cost_difference_usd"] == "0.005015000"
    assert run.config["generation_comparison_sha256"] == report["inputs"]["generation_comparison_sha256"]
    assert run.summary["generation_comparison_sha256"] == run.config["generation_comparison_sha256"]
    assert run.summary["new_image_model_calls"] == 0


def test_generation_default_preparation_does_not_touch_sdk_or_api(generation_report_file, monkeypatch):
    class Forbidden:
        def __getattr__(self, key):
            pytest.fail("generation preparation touched SDK/API")
    original_import = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name == "wandb" or name.startswith("wandb."):
            pytest.fail("generation preparation imported authentication SDK")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    receipt = bridge.sync_cost_report(generation_report_file, entity="team", project="costs",
                                      sdk=Forbidden(), api=Forbidden())
    assert receipt["status"] == "PREPARED" and not receipt["readback_verified"]
    assert receipt["new_image_model_calls"] == 0


def test_omitted_and_explicit_none_generation_preserve_legacy_report_and_wandb_shape(tmp_path, remote):
    from beauty_ai.cost_accounting import canonical_bytes
    from beauty_ai.cost_reporting import build_report
    from test_cost_savings import fixture
    dataset, manifest = fixture()
    legacy = build_report(dataset, manifest)
    explicit_none = build_report(dataset, manifest, generation_comparison=None)
    assert canonical_bytes(legacy) == canonical_bytes(explicit_none)
    assert set(legacy) == bridge._REPORT_FIELDS
    assert set(legacy["inputs"]) == {"dataset_sha256", "comparison_sha256"}
    path = tmp_path / "report.json"
    original = canonical_bytes(legacy) + b"\n"
    path.write_bytes(original)
    assert sync(path, remote)["status"] == "READBACK_VERIFIED"
    run, artifact, _ = remote_parts(remote)
    assert [item.key for item in run.artifacts if item.type == "run_table"] == ["comparison"]
    assert not any(key.startswith("generation_") for key in run.config | run.summary)
    assert artifact.files["report.json"] == original


@pytest.mark.parametrize("change", [
    "result_null", "result_list", "result_missing", "result_extra", "result_schema", "assurance",
    "comparison_id", "rate_version", "arm_null", "arm_missing", "arm_extra", "money_number",
    "money_bool", "money_negative", "ratio_number", "ratio_bool", "reasons_string", "reasons_number",
    "reasons_unsorted", "excluded_null", "excluded_extra", "excluded_bad_id", "excluded_duplicate",
    "excluded_reasons", "inconsistent_counts", "missing_top", "missing_hash", "hash_null", "hash_number",
    "hash_uppercase", "hash_short", "extra_input", "extra_top",
])
def test_invalid_generation_wire_shape_is_rejected_before_auth(generation_report_file, remote, change):
    report = json.loads(generation_report_file.read_bytes())
    result = report["generation_comparison"]
    if change == "result_null":
        report["generation_comparison"] = None
    elif change == "result_list":
        report["generation_comparison"] = []
    elif change == "result_missing":
        del result["coverage_pct"]
    elif change == "result_extra":
        result["extra"] = 1
    elif change == "result_schema":
        result["schema_version"] = "other"
    elif change == "assurance":
        result["assurance"] = "QUALITY_CONTROLLED"
    elif change == "comparison_id":
        result["comparison_id"] = "../unsafe"
    elif change == "rate_version":
        result["estimate_rate_version"] = None
    elif change == "arm_null":
        result["baseline"] = None
    elif change == "arm_missing":
        del result["candidate"]["estimated_cost_per_generation_usd"]
    elif change == "arm_extra":
        result["candidate"]["extra"] = 1
    elif change == "money_number":
        result["baseline"]["total_estimated_cost_usd"] = 1
    elif change == "money_bool":
        result["baseline"]["total_estimated_cost_usd"] = False
    elif change == "money_negative":
        result["baseline"]["total_estimated_cost_usd"] = "-1"
    elif change == "ratio_number":
        result["estimated_cost_reduction_pct"] = 1
    elif change == "ratio_bool":
        result["coverage_pct"] = True
    elif change == "reasons_string":
        result["reason_codes"] = "UNPAIRED_RECORD"
    elif change == "reasons_number":
        result["reason_codes"] = [1]
    elif change == "reasons_unsorted":
        result["reason_codes"] = ["UNPAIRED_RECORD", "COST_MISSING"]
    elif change == "excluded_null":
        result["excluded_records"] = None
    elif change == "excluded_extra":
        result["excluded_records"][0]["extra"] = 1
    elif change == "excluded_bad_id":
        result["excluded_records"][0]["cost_id"] = "../bad"
    elif change == "excluded_duplicate":
        result["excluded_records"][0] = result["excluded_records"][1]
    elif change == "excluded_reasons":
        result["excluded_records"][0]["reason_codes"] = None
    elif change == "inconsistent_counts":
        result["compared_record_count"] = 19
    elif change == "missing_top":
        del report["generation_comparison"]
    elif change == "missing_hash":
        del report["inputs"]["generation_comparison_sha256"]
    elif change.startswith("hash_"):
        report["inputs"]["generation_comparison_sha256"] = {
            "hash_null": None, "hash_number": 1, "hash_uppercase": "A" * 64, "hash_short": "a" * 63,
        }[change]
    elif change == "extra_input":
        report["inputs"]["extra"] = "a" * 64
    elif change == "extra_top":
        report["extra"] = None
    generation_report_file.write_text(json.dumps(report))
    receipt = sync(generation_report_file, remote)
    assert receipt["status"] == "INCOMPLETE" and receipt["error_code"] == "WANDB_COST_REPORT_INVALID"
    assert not remote[0].calls and not remote[1].initializations


@pytest.mark.parametrize("field", [
    "input_record_count", "planned_pair_count", "compared_pair_count", "compared_record_count",
    "excluded_pair_count", "excluded_record_count", "baseline", "candidate",
])
@pytest.mark.parametrize("value", [None, True, False, "1", -1, 2**53 + 1])
def test_generation_counts_require_nonnull_safe_integers(generation_report_file, remote, field, value):
    report = json.loads(generation_report_file.read_bytes())
    result = report["generation_comparison"]
    if field in ("baseline", "candidate"):
        result[field]["generation_count"] = value
    else:
        result[field] = value
    generation_report_file.write_text(json.dumps(report))
    receipt = sync(generation_report_file, remote)
    assert receipt["error_code"] == "WANDB_COST_REPORT_INVALID"
    assert not remote[0].calls and not remote[1].initializations


@pytest.mark.parametrize("key", ["comparison", "generation_cost_estimate"])
@pytest.mark.parametrize("change", ["missing", "value", "bool", "columns", "rows", "duplicate", "wrong_key"])
def test_each_table_is_independently_verified_without_reinitializing(
        generation_report_file, remote, key, change):
    assert sync(generation_report_file, remote)["status"] == "READBACK_VERIFIED"
    run, _, comparison = remote_parts(remote)
    generation_artifact, generation = generation_table(remote)
    artifact = generation_artifact if key == "generation_cost_estimate" else next(
        item for item in run.artifacts if item.type == "run_table" and item.key == key)
    table = generation if key == "generation_cost_estimate" else comparison
    if change == "missing":
        run.artifacts.remove(artifact)
    elif change == "value":
        table.data[0][2] = 99
    elif change == "bool":
        table.data[0][2 if key == "generation_cost_estimate" else 3] = False
    elif change == "columns":
        table.columns.reverse()
    elif change == "rows":
        table.data.append(table.data[0])
    elif change == "duplicate":
        run.artifacts.append(artifact)
    elif change == "wrong_key":
        artifact.get = lambda requested: None
    receipt = sync(generation_report_file, remote)
    assert receipt["status"] == "INCOMPLETE"
    assert receipt["error_code"] == "WANDB_COST_REMOTE_TABLE_MISMATCH"
    assert len(remote[1].initializations) == 1
    assert receipt["previous_verified_receipt"]["status"] == "READBACK_VERIFIED"


@pytest.mark.parametrize("change,code", [
    ("config", "CONFIG"), ("identity", "SUMMARY"), ("summary", "SUMMARY"),
    ("null_summary", "SUMMARY"), ("file", "FILE"),
])
def test_generation_remote_identity_summary_and_original_file_are_verified(
        generation_report_file, remote, change, code):
    assert sync(generation_report_file, remote)["status"] == "READBACK_VERIFIED"
    run, artifact, _ = remote_parts(remote)
    if change == "config":
        run.config["generation_comparison_sha256"] = "d" * 64
    elif change == "identity":
        run.summary["generation_comparison_sha256"] = "d" * 64
    elif change == "summary":
        run.summary["generation_compared_record_count"] = False
    elif change == "null_summary":
        del run.summary["generation_estimated_cost_reduction_pct"]
    elif change == "file":
        artifact.files["report.json"] += b" "
    receipt = sync(generation_report_file, remote)
    assert receipt["status"] == "INCOMPLETE"
    assert receipt["error_code"] == f"WANDB_COST_REMOTE_{code}_MISMATCH"
    assert len(remote[1].initializations) == 1


@pytest.mark.parametrize("baseline,missing", [("0", False), ("1", True)])
def test_valid_generation_unknown_values_remain_null_and_reasons_sorted(tmp_path, remote, baseline, missing):
    from beauty_ai.cost_reporting import build_report
    from test_generation_costs import fixture
    dataset, manifest = fixture(baseline=baseline)
    if missing:
        dataset["cost_items"][0]["estimated_cost_usd"] = None
    report = build_report(dataset, generation_comparison=manifest)
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    assert sync(path, remote)["status"] == "READBACK_VERIFIED"
    _, table = generation_table(remote)
    run, _, _ = remote_parts(remote)
    assert table.data[0][table.columns.index("estimated_cost_reduction_pct")] is None
    assert run.summary["generation_estimated_cost_reduction_pct"] is None
    if missing:
        assert run.summary["generation_compared_record_count"] == 0
        assert run.summary["generation_baseline_generation_count"] == 0
        assert run.summary["generation_baseline_total_estimated_cost_usd"] is None
        assert run.summary["generation_estimated_cost_difference_usd"] is None
        assert run.summary["generation_reason_codes"] == (
            "COST_MISSING,NO_COMPARABLE_PAIRS,PAIRED_RECORD_EXCLUDED")


@pytest.mark.parametrize("fixture_name", ["report_file", "generation_report_file"])
def test_sdk_omitted_null_summaries_verify_and_deduplicate(request, remote, fixture_name):
    path = request.getfixturevalue(fixture_name)
    original = path.read_bytes()
    remote[1].drop_null_summaries = True
    assert sync(path, remote)["status"] == "READBACK_VERIFIED"
    second = sync(path, remote)
    assert second["status"] == "READBACK_VERIFIED" and second["deduplicated"]
    assert len(remote[1].initializations) == 1
    run, artifact, table = remote_parts(remote)
    assert "actual_unit_cost_reduction_pct" not in run.summary
    assert all(value is not None for value in run.summary.values())
    assert artifact.files["report.json"] == original
    assert table.data[1][bridge.TABLE_COLUMNS.index("unit_cost_reduction_pct")] is None


@pytest.mark.parametrize("fixture_name", ["report_file", "generation_report_file"])
@pytest.mark.parametrize("change", ["missing_nonnull", "null_to_zero"])
def test_summary_null_codec_keeps_known_and_unknown_values_distinct(request, remote, fixture_name, change):
    path = request.getfixturevalue(fixture_name)
    remote[1].drop_null_summaries = True
    assert sync(path, remote)["status"] == "READBACK_VERIFIED"
    run, _, _ = remote_parts(remote)
    if change == "missing_nonnull":
        key = ("generation_compared_record_count" if fixture_name == "generation_report_file"
               else "estimated_unit_cost_reduction_pct")
        del run.summary[key]
    else:
        run.summary["actual_unit_cost_reduction_pct"] = 0
    receipt = sync(path, remote)
    assert receipt["status"] == "INCOMPLETE"
    assert receipt["error_code"] == "WANDB_COST_REMOTE_SUMMARY_MISMATCH"
    assert len(remote[1].initializations) == 1


@pytest.mark.parametrize("change,code", [("config", "CONFIG"), ("artifact", "ARTIFACT"), ("table", "TABLE")])
def test_null_summary_codec_does_not_relax_other_readback_checks(generation_report_file, remote, change, code):
    remote[1].drop_null_summaries = True
    assert sync(generation_report_file, remote)["status"] == "READBACK_VERIFIED"
    run, artifact, table = remote_parts(remote)
    if change == "config":
        del run.config["comparison_sha256"]
    elif change == "artifact":
        del artifact.metadata["comparison_sha256"]
    else:
        table.data[1][bridge.TABLE_COLUMNS.index("unit_cost_reduction_pct")] = 0
    receipt = sync(generation_report_file, remote)
    assert receipt["status"] == "INCOMPLETE"
    assert receipt["error_code"] == f"WANDB_COST_REMOTE_{code}_MISMATCH"
    assert len(remote[1].initializations) == 1
