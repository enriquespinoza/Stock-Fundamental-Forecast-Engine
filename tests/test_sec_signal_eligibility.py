import builtins
import csv
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from stock_analyzer import sec_signal_models as models
from test_sec_signal_models import bind_synthetic_cohort, sha


class CohortEligibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.dataset = self.root / "research.csv"
        self.dataset.write_text("event_id,issuer_symbol,issuer_cik,historical_available_after_utc\n"
                                "event-1,AAA,0000000001,2025-01-01T12:00:00Z\n", encoding="utf-8")
        labels = self.root / "labels"
        labels.mkdir()
        label_csv = labels / "labels.csv"
        label_csv.write_text("event_id\nevent-1\n", encoding="utf-8")
        self.write_json(labels / "manifest.json", {"output": {"sha256": sha(label_csv.read_bytes())}})
        self.write_json(self.root / "manifest.json", {
            "forecast_status": "LABELS_AND_FEATURES_NO_FORECAST",
            "output": {"sha256": sha(self.dataset.read_bytes()), "row_count": 1},
            "return_labels_input": {"path": str(label_csv), "sha256": sha(label_csv.read_bytes())},
        })
        bind_synthetic_cohort(self.dataset)

    def write_json(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def manifest(self):
        return json.loads((self.root / "manifest.json").read_text())

    def rows(self):
        with self.dataset.open(newline="", encoding="utf-8") as stream:
            return list(csv.DictReader(stream))

    def repin_policy(self):
        manifest = self.manifest()
        manifest["cohort_input"]["manifest_sha256"] = sha((self.root / "cohort.manifest.json").read_bytes())
        event_path = self.root / "events" / "manifest.json"
        event = json.loads(event_path.read_text())
        event["cohort_input"] = manifest["cohort_input"].copy()
        self.write_json(event_path, event)
        manifest["event_input"]["manifest_sha256"] = sha(event_path.read_bytes())
        self.write_json(self.root / "manifest.json", manifest)

    def repin_event_manifest(self):
        manifest = self.manifest()
        manifest["event_input"]["manifest_sha256"] = sha((self.root / "events" / "manifest.json").read_bytes())
        self.write_json(self.root / "manifest.json", manifest)

    def assert_denied_before_evaluation(self, message="Cohort eligibility"):
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        original_import = builtins.__import__

        def guard_import(name, *args, **kwargs):
            if name.startswith("sklearn"):
                self.fail("Model dependencies were reached before eligibility was denied")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guard_import), \
             patch.object(models, "_read_price_history", side_effect=AssertionError("price processing reached")) as prices, \
             patch.object(models, "_rows_to_vectors", side_effect=AssertionError("features reached")) as features, \
             patch.object(models, "_metrics", side_effect=AssertionError("correlations reached")) as metrics:
            with self.assertRaisesRegex((ValueError, OSError), message):
                models.evaluate_sec_signal_models(self.dataset, self.root / "prices.csv",
                                                  self.root / "prices.manifest.json", self.root / "output")
            prices.assert_not_called()
            features.assert_not_called()
            metrics.assert_not_called()
        self.assertFalse((self.root / "output").exists())
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_permitted_bound_fixture_passes_preflight_without_fitting(self):
        evidence = models._cohort_eligibility(self.manifest(), self.rows())
        self.assertEqual(evidence["cohort_input"], self.manifest()["cohort_input"])
        self.assertEqual(evidence["event_input"]["manifest_sha256"],
                         self.manifest()["event_input"]["manifest_sha256"])
        self.assertTrue(all(value is True for value in evidence["required_permissions"].values()))
        # Reaching price validation proves the entry point accepted the policy;
        # the sentinel stops this synthetic check before features or fitting.
        with patch.object(models, "_read_price_history", side_effect=InterruptedError("authorized preflight")), \
             patch.object(models, "_rows_to_vectors") as features, patch.object(models, "_metrics") as metrics:
            with self.assertRaisesRegex(InterruptedError, "authorized preflight"):
                models.evaluate_sec_signal_models(self.dataset, self.root / "prices.csv",
                                                  self.root / "prices.manifest.json", self.root / "output")
            features.assert_not_called()
            metrics.assert_not_called()
        self.assertFalse((self.root / "output").exists())

    def test_current_pilot_is_rejected_before_fitting(self):
        config = Path(__file__).resolve().parents[1] / "configs" / "universe"
        bind_synthetic_cohort(self.dataset, cohort_path=config / "pilot_sp500_2026-10-07.csv",
                              policy_path=config / "pilot_sp500_2026-10-07.manifest.json")
        self.assert_denied_before_evaluation("permissions denied: correlations_allowed, "
                                            "forecast_training_allowed, historical_performance_claims_allowed")

    def test_each_permission_requires_literal_true(self):
        policy_path = self.root / "cohort.manifest.json"
        original = json.loads(policy_path.read_text())
        for permission in models.EVALUATION_PERMISSIONS:
            for value in (False, None, 1, "true", "yes", [], {}):
                with self.subTest(permission=permission, value=value):
                    policy = json.loads(json.dumps(original))
                    policy["selection"][permission] = value
                    self.write_json(policy_path, policy)
                    self.repin_policy()
                    self.assert_denied_before_evaluation(permission)
            policy = json.loads(json.dumps(original))
            del policy["selection"][permission]
            self.write_json(policy_path, policy)
            self.repin_policy()
            self.assert_denied_before_evaluation(permission)

    def test_malformed_policy_is_denied(self):
        path = self.root / "cohort.manifest.json"
        original = json.loads(path.read_text())
        for policy in ([], {}, {**original, "selection": []}, {**original, "selection": None}):
            with self.subTest(policy=policy):
                self.write_json(path, policy)
                self.repin_policy()
                self.assert_denied_before_evaluation()

    def test_missing_lineage_bindings_are_denied_without_relabeling(self):
        original = self.manifest()
        for group, key in (("cohort_input", None), ("event_input", None),
                           ("cohort_input", "path"), ("cohort_input", "sha256"),
                           ("cohort_input", "manifest_path"), ("cohort_input", "manifest_sha256"),
                           ("event_input", "path"), ("event_input", "sha256"),
                           ("event_input", "manifest_sha256")):
            with self.subTest(group=group, key=key):
                manifest = json.loads(json.dumps(original))
                if key is None:
                    del manifest[group]
                else:
                    del manifest[group][key]
                self.write_json(self.root / "manifest.json", manifest)
                self.assert_denied_before_evaluation()

    def test_changed_bound_bytes_and_substituted_policy_are_denied(self):
        for relative in ("cohort.csv", "cohort.manifest.json", "events/events.csv", "events/manifest.json"):
            with self.subTest(file=relative):
                path = self.root / relative
                raw = path.read_bytes()
                path.write_bytes(raw + b"\n")
                self.assert_denied_before_evaluation("SHA-256 mismatch")
                path.write_bytes(raw)
        # An unrelated permissive manifest with the same universe_id is not a
        # substitute for the exact policy bytes bound when the dataset was built.
        alternate = self.root / "other-policy.json"
        policy = json.loads((self.root / "cohort.manifest.json").read_text())
        self.write_json(alternate, {**policy, "selection_note": "unrelated run"})
        manifest = self.manifest()
        manifest["cohort_input"]["manifest_path"] = str(alternate)
        self.write_json(self.root / "manifest.json", manifest)
        self.assert_denied_before_evaluation("cohort manifest SHA-256 mismatch")

    def test_event_manifest_must_bind_the_same_cohort_and_output(self):
        path = self.root / "events" / "manifest.json"
        original = json.loads(path.read_text())
        for mutation in ("missing", "cohort_hash", "policy_hash", "path", "output_hash", "row_count"):
            with self.subTest(mutation=mutation):
                event = json.loads(json.dumps(original))
                if mutation == "missing":
                    del event["cohort_input"]
                elif mutation == "cohort_hash":
                    event["cohort_input"]["sha256"] = "0" * 64
                elif mutation == "policy_hash":
                    event["cohort_input"]["manifest_sha256"] = "0" * 64
                elif mutation == "path":
                    event["cohort_input"]["path"] = str(self.root / "other.csv")
                elif mutation == "output_hash":
                    event["output"]["sha256"] = "0" * 64
                else:
                    event["output"]["row_count"] = 2
                self.write_json(path, event)
                self.repin_event_manifest()
                self.assert_denied_before_evaluation("eligibility|CSV.*manifest")

    def test_cohort_declaration_and_membership_are_checked(self):
        path = self.root / "cohort.manifest.json"
        original = json.loads(path.read_text())
        for replacement in ({"cohort_csv": str(self.root / "other.csv")}, {"member_count": 2}):
            with self.subTest(replacement=replacement):
                self.write_json(path, {**original, **replacement})
                self.repin_policy()
                self.assert_denied_before_evaluation()
        self.write_json(path, original)
        self.repin_policy()
        # Even hash-consistent inputs cannot claim membership for another CIK.
        self.dataset.write_text(self.dataset.read_text().replace("0000000001", "0000000002"))
        manifest = self.manifest()
        manifest["output"]["sha256"] = sha(self.dataset.read_bytes())
        self.write_json(self.root / "manifest.json", manifest)
        bind_synthetic_cohort(self.dataset, cohort_path=self.root / "cohort.csv", policy_path=path)
        self.assert_denied_before_evaluation("outside the bound cohort")

    def test_dataset_and_event_identity_must_agree(self):
        path = self.root / "events" / "events.csv"
        path.write_text(path.read_text().replace("0000000001", "0000000002"))
        event_path = path.parent / "manifest.json"
        event = json.loads(event_path.read_text())
        event["output"]["sha256"] = sha(path.read_bytes())
        self.write_json(event_path, event)
        manifest = self.manifest()
        manifest["event_input"]["sha256"] = sha(path.read_bytes())
        self.write_json(self.root / "manifest.json", manifest)
        self.repin_event_manifest()
        self.assert_denied_before_evaluation("outside the bound cohort")

    def test_relative_references_keep_repository_cwd_semantics(self):
        manifest = self.manifest()
        policy_path = self.root / "cohort.manifest.json"
        policy = json.loads(policy_path.read_text())
        policy["cohort_csv"] = os.path.relpath(self.root / "cohort.csv")
        self.write_json(policy_path, policy)
        self.repin_policy()
        manifest = self.manifest()
        for key in ("path", "manifest_path"):
            manifest["cohort_input"][key] = os.path.relpath(manifest["cohort_input"][key])
        manifest["event_input"]["path"] = os.path.relpath(manifest["event_input"]["path"])
        self.write_json(self.root / "manifest.json", manifest)
        self.assertEqual(models._cohort_eligibility(manifest, self.rows())["cohort_input"]["path"],
                         str(self.root / "cohort.csv"))
