import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from notebook_loader import load_notebook


class BaselineIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.n = load_notebook()

    def comments(self, **overrides):
        records = []
        for i in range(4):
            args = dict(_platform="youtube", user_id="u", post_id=f"c{i}", source_post_id="p",
                        thread_id=f"c{i}", text_content="a valid sufficiently long text", clean_text="text",
                        created_at=f"2026-01-0{i+1}T00:00:00Z", _collected_at="2026-02-01T00:00:00Z",
                        is_reply=False)
            args.update(overrides)
            records.append(self.n["empty_record"](**args))
        d = self.n["derive_features"](pd.DataFrame(records))
        return d.merge(self.n["coordination_features"](d), on="global_user_id", how="left")

    def model_frame(self):
        d = pd.DataFrame({"_platform": ["youtube"]*6 + ["tiktok"]*6})
        for name, spec in self.n["BASELINE_PROPERTIES"].items():
            if "integer" in spec["type"]:
                d[name] = np.arange(12) % 4 + max(1, spec["minimum"])
            else:
                d[name] = np.linspace(0.1, 0.9, 12)
            d[name + "__status"] = "observed"
        return d

    def test_standard_columns_and_insufficient_peer_support(self):
        a = self.n["aggregate_accounts"](self.comments())
        names = self.n["BASELINE_ACCOUNT_FEATURE_COLUMNS"]
        self.assertEqual(len(names), 13)
        self.assertTrue(set(names).issubset(a.columns))
        self.assertEqual(a.comment_count.iloc[0], 4)
        self.assertTrue(a.recurring_peer_count.isna().all())
        self.assertEqual(a.recurring_peer_count__status.iloc[0], "insufficient_support")
        self.assertEqual(a.mean_urls_per_comment.iloc[0], 0)
        self.n["validate_baseline"](a)

    def test_missing_text_and_confirmed_platform_gap(self):
        a = self.n["aggregate_accounts"](self.comments(text_content=None),
            {"youtube": {"median_comment_interval_seconds": "structural_missing"}})
        self.assertTrue(a.mean_comment_length_chars.isna().all())
        self.assertEqual(a.mean_comment_length_chars__status.iloc[0], "unknown")
        self.assertTrue(a.median_comment_interval_seconds.isna().all())
        self.assertEqual(a.median_comment_interval_seconds__status.iloc[0], "structural_missing")

    def test_observed_empty_text_and_unknown_text_are_distinct(self):
        a = self.n["aggregate_accounts"](self.comments(text_content="", clean_text="", _text_observed=True))
        self.assertEqual(a.mean_comment_length_chars.iloc[0], 0)
        self.assertEqual(a.mean_comment_length_chars__status.iloc[0], "observed")

    def test_strict_json_and_value_status_consistency(self):
        a = self.n["aggregate_accounts"](self.comments())
        frame = self.n["baseline_export"](a, "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z")
        record = list(self.n["baseline_json_records"](frame))[0]
        self.assertEqual(len(record["features"]), 13)
        self.assertIsNone(record["features"]["recurring_peer_count"])
        self.assertEqual(record["account_id"], "youtube:u")
        with self.assertRaises(ValueError):
            list(self.n["baseline_json_records"](self.n["baseline_export"](a)))
        frame.loc[0, "reply_ratio__status"] = "unknown"
        with self.assertRaises(ValueError): self.n["validate_baseline"](frame)

    def test_shared_selection_and_inference_missingness(self):
        x = self.model_frame()
        x.loc[x._platform.eq("tiktok"), "median_comment_interval_seconds"] = np.nan
        x.loc[x._platform.eq("tiktok"), "median_comment_interval_seconds__status"] = "structural_missing"
        x["comment_interval_burstiness"] = np.nan
        x["comment_interval_burstiness__status"] = "insufficient_support"
        prep = self.n["BaselinePreprocessor"]().fit(x)
        self.assertNotIn("median_comment_interval_seconds", prep.selected_features_)
        self.assertNotIn("comment_interval_burstiness", prep.selected_features_)
        before = prep.medians_.copy()
        valid = x.iloc[:2].copy()
        valid["mean_urls_per_comment"] = np.nan
        valid["mean_urls_per_comment__status"] = "collection_failed"
        values = prep.transform(valid)
        self.assertTrue(np.isfinite(values).all())
        self.assertEqual(values.shape[1], 2*len(prep.selected_features_))
        pd.testing.assert_series_equal(before, prep.medians_)

    def test_platform_medians_and_explicit_fallback(self):
        x = self.model_frame()
        prep = self.n["BaselinePreprocessor"](strategy="platform_median").fit(x)
        valid = x.iloc[[0]].copy()
        valid["reply_ratio"] = np.nan
        valid["reply_ratio__status"] = "collection_failed"
        transformed = prep.transform(valid)
        col = prep.selected_features_.index("reply_ratio")
        expected = (x.loc[x._platform.eq("youtube"), "reply_ratio"].median() - prep.means_["reply_ratio"])/prep.scales_["reply_ratio"]
        self.assertAlmostEqual(transformed[0, col], expected)
        valid["_platform"] = "instagram"
        with self.assertRaises(ValueError): prep.transform(valid)
        fallback = self.n["BaselinePreprocessor"](strategy="platform_median", allow_pooled_fallback=True).fit(x)
        self.assertTrue(np.isfinite(fallback.transform(valid)).all())

    def test_native_preserves_nulls_and_model_factory_fits(self):
        x = self.model_frame()
        x.loc[0, "reply_ratio"] = np.nan
        x.loc[0, "reply_ratio__status"] = "unknown"
        native = self.n["BaselinePreprocessor"](strategy="native", add_missing_indicators=False).fit(x)
        values = native.transform(x)
        self.assertTrue(np.isnan(values[0, native.selected_features_.index("reply_ratio")]))
        model = self.n["make_baseline_model"]().fit(x, [0, 1]*6)
        self.assertTrue(np.isfinite(model.predict_proba(x)).all())

    def test_window_filter_precedes_counts_and_exports(self):
        with tempfile.TemporaryDirectory() as folder:
            old = {name: self.n.get(name) for name in ["CANONICAL", "FEATURES"]}
            self.n.update(CANONICAL=Path(folder), FEATURES=Path(folder))
            try:
                self.n["write_csv_with_lists"](self.comments()[self.n["CANONICAL_FIELDS"]], Path(folder)/"youtube.csv")
                self.n["assemble"](platforms=("youtube",), window_start="2026-01-02T00:00:00Z", window_end="2026-01-04T00:00:00Z")
                exported = self.n["read_csv_with_lists"](Path(folder)/"baseline_accounts.csv")
                self.assertEqual(exported.comment_count.iloc[0], 2)
                self.assertEqual(len((Path(folder)/"baseline_accounts.jsonl").read_text().splitlines()), 1)
                self.assertTrue((Path(folder)/"baseline_feature_quality.csv").exists())
                self.n["assemble"](platforms=("youtube",))
                self.assertEqual((Path(folder)/"baseline_accounts.jsonl").read_text(), "")
            finally:
                self.n.update(old)


if __name__ == "__main__":
    unittest.main()
