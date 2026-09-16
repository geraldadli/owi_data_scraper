import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from notebook_loader import load_notebook


class ProfileAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.n = load_notebook()

    def page(self, user=None, stats=None, status=200, broken=False):
        payload = {"__DEFAULT_SCOPE__": {"webapp.user-detail": {"userInfo": {"user": user or {}, "stats": stats or {}}}}}
        def content(**kwargs):
            if broken: raise TimeoutError("unavailable script")
            return json.dumps(payload)
        return SimpleNamespace(goto=lambda *a, **kw: SimpleNamespace(status=status),
            wait_for_timeout=lambda *a: None, locator=lambda *a: SimpleNamespace(text_content=content))

    def record(self, **fields):
        base = dict(_platform="tiktok", user_id="42", username="name", post_id="c1", thread_id="c1",
                    source_post_id="video", created_at="2026-01-01T00:00:00Z", _collected_at="2026-02-01T00:00:00Z",
                    text_content="an observed sample comment", clean_text="an observed sample comment", is_reply=False)
        base.update(fields)
        return self.n["empty_record"](**base)

    def test_private_public_and_absent_flag(self):
        for flag, visibility, access in [(True,"private","restricted"),(False,"public","readable"),(None,"unknown","readable")]:
            with self.subTest(flag=flag):
                user = {"id":"42", "uniqueId":"name"}
                if flag is not None: user["privateAccount"] = flag
                result = self.n["_fetch_tiktok_profile"](self.page(user), "name", expected_user_id="42")
                self.assertEqual(result["profile_visibility"], visibility)
                self.assertEqual(result["profile_access_status"], access)
                self.assertIsNone(result["followers_count"])
                self.assertIsNotNone(result["profile_checked_at"])

    def test_errors_and_mismatches_never_prove_privacy(self):
        private = {"id":"42", "uniqueId":"name", "privateAccount":True}
        pages = [self.page(private,status=403), self.page(broken=True),
                 self.page({**private,"id":"999"}), self.page({**private,"uniqueId":"other"}), self.page()]
        for page in pages:
            result=self.n["_fetch_tiktok_profile"](page,"name",expected_user_id="42")
            self.assertEqual(result["profile_visibility"],"unknown")
            self.assertEqual(result["profile_access_status"],"failed")
            self.assertNotIn("_profile_user_id",result)

    def test_failed_refresh_retains_stats_and_separates_check_date(self):
        old={"_profile_user_id":"42","followers_count":10,"_profile_collected_at":"2026-01-01T00:00:00Z",
             **self.n["profile_audit"]({"privateAccount":True},True,"2026-01-01T00:00:00Z")}
        failed={"_requested_user_id":"42",**self.n["profile_audit"](checked_at="2026-02-01T00:00:00Z")}
        merged=self.n["merge_profile_attempt"](old,failed)
        self.assertEqual(merged["followers_count"],10)
        self.assertEqual(merged["_profile_collected_at"],"2026-01-01T00:00:00Z")
        self.assertEqual(merged["profile_checked_at"],"2026-02-01T00:00:00Z")
        self.assertEqual(merged["profile_visibility"],"unknown")
        self.assertEqual(merged["_profile_status"],"stale_cache")
        wrong=self.n["merge_profile_attempt"](old,{**failed,"_requested_user_id":"999"})
        self.assertNotIn("followers_count",wrong)

    def test_failure_audit_merges_only_to_requested_account(self):
        df=pd.DataFrame([self.record(),self.record(user_id="999",post_id="c2")])
        result=self.n["merge_profile_enrichment"](df,{"name":{
            "_requested_user_id":"42",**self.n["profile_audit"]()}})
        self.assertEqual(result.profile_access_status.iloc[0],"failed")
        self.assertTrue(pd.isna(result.profile_access_status.iloc[1]))
        self.assertTrue(result.followers_count.isna().all())

    def test_audit_cache_roundtrip_and_freshness(self):
        with tempfile.TemporaryDirectory() as folder:
            old=self.n.get("RAW");self.n["RAW"]=Path(folder)
            try:
                row={"username":"name","_profile_user_id":"42","_profile_collected_at":self.n["now_utc"](),
                     **self.n["profile_audit"]({"privateAccount":True},True)}
                self.n["jsonl_append"](Path(folder)/"tiktok_profiles.jsonl",[row])
                cached=self.n["_load_profile_cache"]("tiktok")["name"]
                self.assertEqual(cached["profile_visibility"],"private")
                self.assertEqual(cached["profile_checked_at"],row["profile_checked_at"])
                self.assertTrue(self.n["profile_cache_fresh"](cached))
                cached["profile_access_status"]="failed"
                self.assertFalse(self.n["profile_cache_fresh"](cached))
            finally:self.n["RAW"]=old

    def test_export_latest_audit_and_exclusion_from_features(self):
        old=self.record(_profile_collected_at="2026-02-01T00:00:00Z",
            **self.n["profile_audit"]({"privateAccount":True},True,"2026-02-01T00:00:00Z"))
        newer=self.record(post_id="c2",_profile_collected_at="2026-01-01T00:00:00Z",
            **self.n["profile_audit"](checked_at="2026-03-01T00:00:00Z"))
        d=self.n["derive_features"](pd.DataFrame([old,newer]))
        d=d.merge(self.n["coordination_features"](d),on="global_user_id")
        a=self.n["aggregate_accounts"](d)
        self.assertEqual(a.profile_access_status.iloc[0],"failed")
        self.assertEqual(a.profile_checked_at.iloc[0],"2026-03-01T00:00:00Z")
        baseline=self.n["baseline_export"](a,"2026-01-01T00:00:00Z","2026-04-01T00:00:00Z")
        record=list(self.n["baseline_json_records"](baseline))[0]
        self.assertEqual(record["profile_visibility"],"unknown")
        self.assertEqual(len(record["features"]),13)
        for name in self.n["PROFILE_AUDIT_FIELDS"]:
            self.assertIn(name,self.n["ID_COLUMNS"])
            self.assertNotIn(name,self.n["BASELINE_ACCOUNT_FEATURE_COLUMNS"])
            self.assertNotIn(name,self.n["ACCOUNT_FEATURE_COLUMNS"])
        public=self.n["pseudonymise"](baseline,"test-salt")
        self.assertNotIn("profile_checked_at",public)
        self.assertIn("profile_visibility",public)

    def test_unchecked_legacy_accounts_remain_unknown(self):
        d=self.n["derive_features"](pd.DataFrame([self.record()]))
        self.assertEqual(d.profile_visibility.iloc[0],"unknown")
        self.assertTrue(pd.isna(d.profile_access_status.iloc[0]))
        self.assertTrue(pd.isna(d.profile_checked_at.iloc[0]))


if __name__ == "__main__": unittest.main()
