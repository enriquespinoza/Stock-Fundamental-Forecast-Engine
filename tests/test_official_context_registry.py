import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "configs" / "sources" / "official_context_registry_v2.json"


class OfficialContextRegistryTests(unittest.TestCase):
    def test_registry_entries_have_point_in_time_and_scope_metadata(self):
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(registry["schema_version"], "official-context-source-registry-v2")
        self.assertEqual(registry["registry_status"], "ACTIVE_NO_MACROECONOMIC_INDICATORS")
        self.assertTrue(registry["research_only"])
        entries = registry["entries"]
        ids = [entry["source_id"] for entry in entries]
        self.assertEqual(len(ids), len(set(ids)))
        required = {
            "publisher", "source_class", "product", "url", "scope", "cohort_sectors",
            "observation_date_field", "public_availability_field", "point_in_time_policy",
            "revision_policy", "series_selection", "limitations",
        }
        for entry in entries:
            with self.subTest(source_id=entry["source_id"]):
                self.assertTrue(required.issubset(entry))
                self.assertTrue(entry["url"].startswith("https://"))
                self.assertTrue(entry["point_in_time_policy"])
                self.assertTrue(entry["revision_policy"])
                self.assertTrue(entry["limitations"])

    def test_registry_separates_discovery_and_context_from_company_facts(self):
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        entries = {entry["source_id"]: entry for entry in registry["entries"]}
        self.assertEqual(entries["sec_edgar_company_disclosures"]["source_class"],
                         "COMPANY_REPORTED_PRIMARY")
        self.assertEqual(entries["datagov_discovery_catalog"]["source_class"], "DISCOVERY_ONLY")
        self.assertEqual(entries["eia_petroleum_price_context"]["cohort_sectors"], ["Energy"])
        excluded = set(registry["excluded_v1_source_ids"])
        self.assertTrue({"treasury_daily_par_yield_curve", "bls_cpi_u_all_items",
                         "bls_ppi_final_demand", "bea_industry_accounts",
                         "fred_alfred_vintage_access", "ofr_financial_system_context"}.issubset(excluded))
        self.assertFalse(excluded.intersection(entries))


if __name__ == "__main__":
    unittest.main()
