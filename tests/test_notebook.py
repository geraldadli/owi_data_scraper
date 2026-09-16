import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from notebook_loader import load_notebook


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.n = load_notebook()

    def record(self, user="u", comment="c", platform="youtube", source="video1", **extra):
        values = dict(user_id=user, post_id=comment, _platform=platform, source_post_id=source,
                      thread_id=comment, text_content="a sufficiently long message", clean_text="message",
                      _collected_at="2026-01-02T00:00:00Z", created_at="2026-01-01T00:00:00Z",
                      is_reply=False, media_types=["text"])
        values.update(extra)
        return self.n["empty_record"](**values)

    def features(self, records):
        d = self.n["derive_features"](pd.DataFrame(records))
        return d.merge(self.n["coordination_features"](d), on="global_user_id", how="left")

    def test_three_column_scraping_export_all_platforms(self):
        with tempfile.TemporaryDirectory() as folder:
            old = {k: self.n.get(k) for k in ["CANONICAL", "FEATURES"]}
            self.n.update(CANONICAL=Path(folder), FEATURES=Path(folder))
            try:
                for platform in ("youtube", "tiktok", "instagram", "x", "facebook"):
                    frame = pd.DataFrame([
                        self.record(platform=platform, like_count=0, reply_count=None,
                                    created_at="2026-01-01T07:00:00+07:00"),
                        self.record(platform=platform, comment="d", like_count=12,
                                    reply_count=3, created_at=None),
                    ])
                    self.n["save_canonical"](frame, platform)
                    out = self.n["load_canonical"](platform)
                    self.assertEqual(list(out.columns), ["like_count", "reply_count", "date_published"])
                    self.assertEqual(out.like_count.iloc[0], 0)
                    self.assertTrue(pd.isna(out.reply_count.iloc[0]))
                    self.assertEqual(pd.Timestamp(out.date_published.iloc[0]), pd.Timestamp("2026-01-01T00:00:00Z"))
                    self.assertTrue(pd.isna(out.date_published.iloc[1]))
                combined = self.n["assemble_scraped_data"]()
                self.assertEqual(len(combined), 10)
                self.assertEqual(list(combined.columns), ["like_count", "reply_count", "date_published"])
                self.assertFalse((Path(folder)/"dataset_accounts.csv").exists())
            finally:
                self.n.update(old)

    def test_every_cell_compiles(self):
        nb = json.loads((Path(__file__).resolve().parents[1]/"buzzer.ipynb").read_text(encoding="utf-8"))
        for i, cell in enumerate(nb["cells"]):
            if cell["cell_type"] == "code": compile("".join(cell["source"]), f"cell-{i}", "exec")

    def test_csv_large_ids_missing_values_and_literal_na(self):
        records = [self.record(user="0018446744073709551615", username="NA"), self.record(user=None, comment="d")]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"data.csv"
            self.n["write_csv_with_lists"](pd.DataFrame(records), path)
            d = self.n["read_csv_with_lists"](path)
        self.assertEqual(d.user_id.iloc[0], records[0]["user_id"])
        self.assertEqual(d.username.iloc[0], "NA")
        self.assertTrue(pd.isna(d.user_id.iloc[1]))
        self.assertEqual(d.media_types.iloc[0], ["text"])

    def test_missing_accounts_excluded_and_snapshots_deduplicated(self):
        d = self.n["derive_features"](pd.DataFrame([self.record(), self.record(), self.record(user=None,comment="deleted")]))
        self.assertEqual(len(d), 1)
        self.assertEqual(d.duplicate_text_count.iloc[0], 1)

    def test_empty_bio_and_unknown_bio_survive_csv(self):
        records=[self.record(bio_text="", _collector_version="local-e2e-1.1.0"),
                 self.record(comment="d", bio_text=None, _collector_version="local-e2e-1.1.0")]
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"records.csv"
            self.n["write_csv_with_lists"](pd.DataFrame(records),path)
            d=self.n["read_csv_with_lists"](path)
        self.assertEqual(d.bio_text.iloc[0],"")
        self.assertTrue(pd.isna(d.bio_text.iloc[1]))

    def test_different_youtube_sources_count_separately(self):
        d = self.features([self.record(source=None, _source_url="https://youtube.com/watch?v=ONE&t=1"),
                           self.record(comment="d", source=None, _source_url="https://youtube.com/watch?v=TWO")])
        a = self.n["aggregate_accounts"](d)
        self.assertEqual(a.n_source_posts.iloc[0], 2)

    def test_age_is_reproducible_and_missing_is_not_false(self):
        d = self.n["derive_features"](pd.DataFrame([self.record(account_created_at="2026-01-01T00:00:00Z"),
            self.record(comment="d", is_verified="False", bio_text="", username="user123456",following_count=0,followers_count=0)]))
        self.assertEqual(d.account_age_days.iloc[0], 1)
        self.assertTrue(pd.isna(d.is_verified.iloc[0]))
        self.assertFalse(d.is_verified.iloc[1])
        self.assertTrue(pd.isna(d.bio_length.iloc[0]))
        self.assertEqual(d.bio_length.iloc[1], 0)
        self.assertEqual(d.follower_to_following_ratio.iloc[1], 1)
        self.assertEqual(d.media_count.iloc[0], 0)

    def test_dedupe_preserves_message_content(self):
        f = self.n["dedupe_key"]
        self.assertNotEqual(f("a+b"), f("ab"))
        self.assertNotEqual(f("😀"), f(""))
        self.assertEqual(f("Hello   WORLD"), f("hello world"))

    def test_entities_do_not_erase_existing_structured_urls(self):
        r = self.n["enrich_text_fields"](dict(text_content="hi @person #hello", urls=["https://example.org"]))
        self.assertEqual(r["urls"], ["https://example.org"])
        self.assertEqual(r["user_mentions"], ["person"])

    def test_platform_and_source_isolation(self):
        d = self.features([self.record(user="a", platform="youtube", thread_id="same"),
                           self.record(user="b", platform="tiktok", thread_id="same")])
        self.assertTrue(d.thread_co_partners.eq(0).all())
        self.assertTrue(d.distinct_users_same_text.eq(1).all())

    def test_post_coordination_and_budget_is_explicit(self):
        records = [self.record(user=u, comment=u+v, source=v) for u in ["a","b","c"] for v in ["one","two"]]
        d = self.n["derive_features"](pd.DataFrame(records))
        co = self.n["coordination_features"](d)
        self.assertTrue(co.post_co_partners.eq(2).all())
        self.assertTrue(co.post_co_rate.eq(1).all())
        bounded = self.n["coordination_features"](d, max_pair_events=0)
        self.assertTrue(bounded.post_co_partners.isna().all())
        self.assertTrue(bounded.post_co_skipped_groups.eq(2).all())

    def test_latest_profile_does_not_mix_dates(self):
        d = self.features([self.record(followers_count=10, following_count=2),
             self.record(comment="later", followers_count=20, following_count=None, _collected_at="2026-01-03T00:00:00Z")])
        a = self.n["aggregate_accounts"](d)
        self.assertEqual(a.followers_count.iloc[0], 20)
        self.assertTrue(pd.isna(a.following_count.iloc[0]))

    def test_temporal_features_require_observations(self):
        d = self.features([self.record(comment=f"c{i}", created_at=f"2026-01-01T00:00:0{i}Z") for i in range(4)])
        a = self.n["aggregate_accounts"](d)
        self.assertEqual(a.median_intercomment_seconds.iloc[0], 1)
        self.assertEqual(a.intercomment_burstiness.iloc[0], -1)
        self.assertEqual(a.n_timing_intervals.iloc[0], 3)

    def test_tiktok_embedded_replies_identity_and_capture_time(self):
        child = dict(cid="2", text="reply", reply_id="1", create_time="1700000000", user={"uid":"7","sec_uid":"sec7"})
        row = dict(_page_url="https://www.tiktok.com/@u/video/123",_captured_at="2026-01-01T00:00:00Z",
                   payload={"comments":[dict(cid="1", text="top", reply_id="0", user={"uid":"8"}, reply_comment=[child])]})
        parsed = self.n["parse_tiktok"](row)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[1]["user_id"], "7")
        self.assertEqual(parsed[1]["thread_id"], "1")
        self.assertEqual(parsed[1]["source_post_id"], "123")
        self.assertEqual(parsed[1]["_collected_at"], row["_captured_at"])

    def test_instagram_handles_both_lists_and_false_verification(self):
        row = dict(_url="https://instagram.com/api/v1/media/3/comments/1/child_comments/",
                   _page_url="https://instagram.com/p/SHORT/", payload={
                       "comments":[dict(pk="1",text="top",user={"pk":"u","is_verified":False})],
                       "child_comments":[dict(pk="2",text="child",user={"pk":"v"})]})
        parsed = self.n["parse_instagram"](row)
        self.assertEqual(len(parsed), 2)
        self.assertFalse(parsed[0]["is_verified"])
        self.assertEqual(parsed[1]["parent_comment_id"], "1")

    def test_x_excludes_root_quotes_and_unrelated_tweets(self):
        def tweet(tid, conv, parent=None):
            return dict(rest_id=tid, legacy=dict(full_text="text", conversation_id_str=conv, in_reply_to_status_id_str=parent),
                        core={"user_results":{"result":{"rest_id":"u","legacy":{}}}})
        reply=tweet("2","1","1");reply["quoted_status_result"]={"result":tweet("3","3")}
        row=dict(_platform="x", _page_url="https://x.com/u/status/1",payload=[tweet("1","1"),reply,tweet("4","9","9")])
        parsed=self.n["parse_x"](row)
        self.assertEqual([r["post_id"] for r in parsed],["2"])
        self.assertEqual(self.n["coverage_parts"](row)[0],{"2"})

    def test_facebook_feedback_is_not_reply_parent(self):
        row=dict(_page_url="https://facebook.com/u/posts/9",payload={"__typename":"Comment","id":"c", "body":{"text":"hi"},
                "author":{"id":"u","is_verified":False}, "parent_feedback":{"id":"post-feedback"},"created_time":"2026-01-01T00:00:00Z"})
        parsed=self.n["parse_facebook"](row)[0]
        self.assertFalse(parsed["is_reply"])
        self.assertIsNone(parsed["parent_comment_id"])

    def test_coverage_is_incremental_and_resets_per_target(self):
        h=self.n["Harvester"]("tiktok")
        h.page=SimpleNamespace(goto=lambda *a,**kw:None,wait_for_timeout=lambda *a:None)
        def payload(cid): return {"payload":{"comments":[{"cid":cid,"text":"x"}],"total":10},"_url":"/api/comment/list"}
        h.open("https://tiktok.com/@u/video/1",1)
        h.captured.append(payload("a"))
        self.assertTrue(h._at_cap())
        cursor=h._coverage_cursor
        self.assertEqual(h._coverage_snapshot(),(1,10));self.assertEqual(cursor,h._coverage_cursor)
        h.open("https://tiktok.com/@u/video/2",1)
        self.assertEqual(h._coverage_snapshot(),(0,None))
        self.assertFalse(h._at_cap())
        h.captured.append(payload("b"));self.assertEqual(h._coverage_snapshot(),(1,10))

    def test_profile_identity_mismatch_is_not_merged(self):
        df=pd.DataFrame([self.record(user="stable1",username="name")])
        profiles={"name":{"followers_count":100,"_profile_user_id":"stable2"}}
        result=self.n["merge_profile_enrichment"](df,profiles)
        self.assertTrue(pd.isna(result.followers_count.iloc[0]))
        profiles["name"]["_profile_user_id"]="stable1"
        result=self.n["merge_profile_enrichment"](df,profiles)
        self.assertEqual(result.followers_count.iloc[0],100)

    def test_public_ids_are_joinable_and_raw_locators_removed(self):
        d=self.features([self.record()])
        a=self.n["aggregate_accounts"](d)
        public=self.n["pseudonymise"](d,"salt")
        public_acc=self.n["pseudonymise"](a,"salt")
        self.assertEqual(public.global_user_id.iloc[0],public_acc.global_user_id.iloc[0])
        self.assertEqual(public.post_id.iloc[0],public.thread_id.iloc[0])
        self.assertNotEqual(public.post_id.iloc[0],"c")
        self.assertNotIn("_source_url",public);self.assertNotIn("created_at",public)

    def test_youtube_reply_pagination_and_comment_id(self):
        def comment(cid): return {"id":cid,"snippet":{"textOriginal":"test","parentId":"top"}}
        top={"id":"THREAD_RESOURCE_ID","snippet":{"topLevelComment":comment("top"),"totalReplyCount":3},
             "replies":{"comments":[comment("r1")]}}
        calls=[]
        class API:
            def commentThreads(self):
                return SimpleNamespace(list=lambda **kw:SimpleNamespace(execute=lambda:{"items":[top]}))
            def comments(self):
                def query(**kw):
                    calls.append(kw)
                    data={"items":[comment("r1"),comment("r2")],"nextPageToken":"next"} if kw["pageToken"] is None else {"items":[comment("r3")]}
                    return SimpleNamespace(execute=lambda:data)
                return SimpleNamespace(list=query)
        self.n["HttpError"]=RuntimeError
        raw=self.n["yt_fetch"]("video",4,yt=API())
        self.assertEqual(len(raw),4);self.assertEqual(len(calls),2)
        self.assertTrue(all(c["parentId"]=="top" for c in calls))
        norm=[self.n["yt_normalise"](r) for r in raw]
        self.assertEqual(norm[0]["post_id"],"top")
        self.assertTrue(all(r["thread_id"]=="top" for r in norm))
        self.assertEqual(len(self.n["yt_fetch"]("video",1,yt=API())),1)

    def test_browser_canonical_hard_cap_and_profile_exclusion(self):
        with tempfile.TemporaryDirectory() as folder:
            old={k:self.n.get(k) for k in ["RAW","CANONICAL"]}
            self.n.update(RAW=Path(folder),CANONICAL=Path(folder))
            try:
                rows=[dict(_capture_session="run",_max_comments=1,_page_url="https://tiktok.com/@u/video/1",_kind="comment",
                      payload={"comments":[dict(cid=str(i),text="text",user={"uid":str(i+10)}) for i in range(1,4)]})]
                rows.append(dict(_kind="profile",payload={"comments":[dict(cid="profile",text="ignore",user={"uid":"99"})]}))
                self.n["jsonl_append"](Path(folder)/"tiktok_payloads.jsonl",rows)
                d=self.n["build_browser_canonical"]("tiktok")
                self.assertEqual(len(d),1)
            finally: self.n.update(old)

    def test_parse_failure_preserves_existing_canonical(self):
        with tempfile.TemporaryDirectory() as folder:
            old={k:self.n.get(k) for k in ["RAW","CANONICAL"]}
            self.n.update(RAW=Path(folder),CANONICAL=Path(folder))
            try:
                canonical=Path(folder)/"tiktok.csv"
                canonical.write_text("previous successful dataset",encoding="utf-8")
                self.n["jsonl_append"](Path(folder)/"tiktok_payloads.jsonl",[{"payload":{"comments":[7]}}])
                with self.assertRaises(ValueError): self.n["build_browser_canonical"]("tiktok")
                self.assertEqual(canonical.read_text(encoding="utf-8"),"previous successful dataset")
            finally: self.n.update(old)

    def test_zero_profile_budget_does_not_open_browser(self):
        with tempfile.TemporaryDirectory() as folder:
            old=self.n.get("RAW");self.n["RAW"]=Path(folder)
            try:
                # No Playwright object is available in the offline namespace.
                self.assertEqual(self.n["enrich_tiktok_profiles"](["name"],max_accounts=0),{})
                self.assertEqual(self.n["enrich_instagram_profiles"](["name"],max_accounts=0),{})
            finally: self.n["RAW"]=old


if __name__ == "__main__":
    unittest.main()
