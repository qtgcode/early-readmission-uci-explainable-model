from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from readmission.pipeline import evaluate, grouped_splits, prepare_cohort, select_f1_threshold


class PipelineTests(unittest.TestCase):
    def test_cohort_exclusions_and_target_definition(self):
        rows = []
        for i, (disp, label) in enumerate([
            ("1", "<30"), ("2", ">30"), ("13", "<30"), ("11", "NO"), ("6", "NO"), ("12", "<30")
        ]):
            rows.append({
                "encounter_id": str(i), "patient_nbr": str(i), "discharge_disposition_id": disp,
                "readmitted": label, "diag_1": "250.00", "diag_2": "401", "diag_3": "?",
                "gender": "Female", "race": "Caucasian",
            })
        raw = pd.DataFrame(rows)
        mapping = """admission_type_id,description\n1,Emergency\n,\ndischarge_disposition_id,description\n1,Discharged to home\n2,Transfer\n6,Home health\n11,Expired\n13,Hospice\n,\nadmission_source_id,description\n7,Emergency room\n"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "IDS_mapping.csv"
            path.write_text(mapping, encoding="utf-8")
            X, y, ids, info = prepare_cohort(raw, path)
        self.assertEqual(len(X), 3)
        self.assertEqual(y.tolist(), [1, 0, 0])
        self.assertEqual(info["excluded_death_dispositions"], 1)
        self.assertEqual(info["excluded_hospice_dispositions"], 1)
        self.assertEqual(info["excluded_pending_discharge_disposition_12"], 1)
        self.assertNotIn("patient_nbr", X.columns)
        self.assertNotIn("encounter_id", X.columns)
        self.assertNotIn("gender", X.columns)
        self.assertNotIn("race", X.columns)
        self.assertIn("gender", info["excluded_columns"])
        self.assertIn("race", info["excluded_columns"])
        self.assertEqual(X.iloc[0]["diag_1"], "Diabetes (250)")
        self.assertEqual(ids.tolist(), ["0", "1", "4"])

    def test_grouped_split_has_disjoint_patients(self):
        groups = pd.Series([f"p{i}" for i in range(40) for _ in range(2)])
        y = pd.Series([i % 2 for i in range(40) for _ in range(2)])
        X = pd.DataFrame({"x": np.arange(len(y))})
        train, val, test = grouped_splits(X, y, groups)
        g_train, g_val, g_test = (set(groups.iloc[idx]) for idx in (train, val, test))
        self.assertFalse(g_train & g_val)
        self.assertFalse(g_train & g_test)
        self.assertFalse(g_val & g_test)

    def test_threshold_and_metrics_are_finite(self):
        y = np.array([0, 0, 1, 1])
        p = np.array([0.1, 0.4, 0.6, 0.9])
        threshold, val_f1 = select_f1_threshold(y, p)
        metrics = evaluate(y, p, threshold)
        self.assertGreaterEqual(threshold, 0)
        self.assertLessEqual(threshold, 1)
        self.assertAlmostEqual(val_f1, 1.0)
        self.assertAlmostEqual(metrics["pr_auc_average_precision"], 1.0)
        self.assertEqual(metrics["tp"], 2)


if __name__ == "__main__":
    unittest.main()
