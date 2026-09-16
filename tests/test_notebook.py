import json
import tempfile
import unittest
import traceback
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
from notebook_loader import load_notebook

class PipelineTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.n = load_notebook()

    def record(self, user='u', comment='c', platform='youtube', source='video1', **extra):
        values = dict(user_id=user, post_id=comment, _platform=platform, source_post_id=source, thread_id=comment, text_content='a sufficiently long message', clean_text='message', _collected_at='2026-01-02T00:00:00Z', created_at='2026-01-01T00:00:00Z', is_reply=False, media_types=['text'])
        values.update(extra)
        return self.n['empty_record'](**values)

    def test_three_column_scraping_export_all_platforms(self):
        with tempfile.TemporaryDirectory() as folder:
            old = {k: self.n.get(k) for k in ['CANONICAL', 'FEATURES']}
            self.n.update(CANONICAL=Path(folder), FEATURES=Path(folder))
            try:
                for platform in ('youtube', 'tiktok', 'instagram', 'x', 'facebook'):
                    frame = pd.DataFrame([self.record(platform=platform, like_count=0, reply_count=None, created_at='2026-01-01T07:00:00+07:00'), self.record(platform=platform, comment='d', like_count=12, reply_count=3, created_at=None)])
                    self.n['save_canonical'](frame, platform)
                    out = self.n['load_canonical'](platform)
                    self.assertEqual(list(out.columns), ['like_count', 'reply_count', 'date_published'])
                    self.assertEqual(out.like_count.iloc[0], 0)
                    self.assertTrue(pd.isna(out.reply_count.iloc[0]))
                    self.assertEqual(pd.Timestamp(out.date_published.iloc[0]), pd.Timestamp('2026-01-01T00:00:00Z'))
                    self.assertTrue(pd.isna(out.date_published.iloc[1]))
                combined = self.n['assemble_scraped_data']()
                self.assertEqual(len(combined), 10)
                self.assertEqual(list(combined.columns), ['like_count', 'reply_count', 'date_published'])
                self.assertFalse((Path(folder) / 'dataset_accounts.csv').exists())
            finally:
                self.n.update(old)

    def test_every_cell_compiles(self):
        nb = json.loads((Path(__file__).resolve().parents[1] / 'buzzer.ipynb').read_text(encoding='utf-8'))
        for i, cell in enumerate(nb['cells']):
            if cell['cell_type'] == 'code':
                compile(''.join(cell['source']), f'cell-{i}', 'exec')

    def test_all_video_lists_and_no_account_scrapers(self):
        expected = {'tiktok': 12, 'x': 11, 'instagram': 11, 'youtube': 12, 'facebook': 10}
        self.assertEqual({p: len(urls) for p, urls in self.n['VIDEO_URLS'].items()}, expected)
        urls = [url for group in self.n['VIDEO_URLS'].values() for url in group]
        self.assertEqual(len(set(urls)), 56)
        self.assertEqual(len(self.n['BROWSER_TARGETS']), 44)
        self.assertEqual(len(self.n['YT_VIDEO_IDS']), 12)
        for removed in ('yt_enrich_channels', 'enrich_tiktok_profiles', 'enrich_instagram_profiles',
                        'aggregate_accounts', 'BaselinePreprocessor', 'PROFILE_ENDPOINTS'):
            self.assertNotIn(removed, self.n)
        self.assertEqual(self.n['Harvester']('instagram').patterns, self.n['COMMENT_ENDPOINTS']['instagram'])

    def test_facebook_likes_are_not_total_reactions(self):
        comment = {'__typename': 'Comment', 'id': '1', 'body': {'text': 'hi'},
                   'author': {}, 'feedback': {'reaction_count': {'count': 99}}}
        self.assertIsNone(self.n['parse_facebook']({'payload': comment})[0]['like_count'])
        comment['feedback']['like_count'] = {'count': 0}
        self.assertEqual(self.n['parse_facebook']({'payload': comment})[0]['like_count'], 0)
        url = 'https://www.facebook.com/page/videos/video-title/123456/'
        self.assertEqual(self.n['source_post_id']('facebook', url), '123456')

    def test_tiktok_embedded_replies_identity_and_capture_time(self):
        child = dict(cid='2', text='reply', reply_id='1', create_time='1700000000', user={'uid': '7', 'sec_uid': 'sec7'})
        row = dict(_page_url='https://www.tiktok.com/@u/video/123', _captured_at='2026-01-01T00:00:00Z', payload={'comments': [dict(cid='1', text='top', reply_id='0', user={'uid': '8'}, reply_comment=[child])]})
        parsed = self.n['parse_tiktok'](row)
        self.assertEqual(len(parsed), 2)
        self.assertNotIn('user_id', parsed[1])
        self.assertEqual(parsed[1]['thread_id'], '1')
        self.assertEqual(parsed[1]['source_post_id'], '123')
        self.assertEqual(parsed[1]['created_at'], '2023-11-14T22:13:20+00:00')

    def test_instagram_handles_both_lists_and_false_verification(self):
        row = dict(_url='https://instagram.com/api/v1/media/3/comments/1/child_comments/', _page_url='https://instagram.com/p/SHORT/', payload={'comments': [dict(pk='1', text='top', user={'pk': 'u', 'is_verified': False})], 'child_comments': [dict(pk='2', text='child', user={'pk': 'v'})]})
        parsed = self.n['parse_instagram'](row)
        self.assertEqual(len(parsed), 2)
        self.assertNotIn('is_verified', parsed[0])
        self.assertEqual(parsed[1]['parent_comment_id'], '1')

    def test_x_excludes_root_quotes_and_unrelated_tweets(self):

        def tweet(tid, conv, parent=None):
            return dict(rest_id=tid, legacy=dict(full_text='text', conversation_id_str=conv, in_reply_to_status_id_str=parent), core={'user_results': {'result': {'rest_id': 'u', 'legacy': {}}}})
        reply = tweet('2', '1', '1')
        reply['quoted_status_result'] = {'result': tweet('3', '3')}
        row = dict(_platform='x', _page_url='https://x.com/u/status/1', payload=[tweet('1', '1'), reply, tweet('4', '9', '9')])
        parsed = self.n['parse_x'](row)
        self.assertEqual([r['post_id'] for r in parsed], ['2'])
        self.assertEqual(self.n['coverage_parts'](row)[0], {'2'})

    def test_facebook_feedback_is_not_reply_parent(self):
        row = dict(_page_url='https://facebook.com/u/posts/9', payload={'__typename': 'Comment', 'id': 'c', 'body': {'text': 'hi'}, 'author': {'id': 'u', 'is_verified': False}, 'parent_feedback': {'id': 'post-feedback'}, 'created_time': '2026-01-01T00:00:00Z'})
        parsed = self.n['parse_facebook'](row)[0]
        self.assertNotIn('user_id', parsed)
        self.assertIsNone(parsed['parent_comment_id'])

    def test_coverage_is_incremental_and_resets_per_target(self):
        h = self.n['Harvester']('tiktok')
        h.page = SimpleNamespace(goto=lambda *a, **kw: None, wait_for_timeout=lambda *a: None)

        def payload(cid):
            return {'payload': {'comments': [{'cid': cid, 'text': 'x'}], 'total': 10}, '_url': '/api/comment/list'}
        h.open('https://tiktok.com/@u/video/1', 1)
        h.captured.append(payload('a'))
        self.assertTrue(h._at_cap())
        cursor = h._coverage_cursor
        self.assertEqual(h._coverage_snapshot(), (1, 10))
        self.assertEqual(cursor, h._coverage_cursor)
        h.open('https://tiktok.com/@u/video/2', 1)
        self.assertEqual(h._coverage_snapshot(), (0, None))
        self.assertFalse(h._at_cap())
        h.captured.append(payload('b'))
        self.assertEqual(h._coverage_snapshot(), (1, 10))

    def test_youtube_reply_pagination_and_comment_id(self):

        def comment(cid):
            return {'id': cid, 'snippet': {'textOriginal': 'test', 'parentId': 'top'}}
        top = {'id': 'THREAD_RESOURCE_ID', 'snippet': {'topLevelComment': comment('top'), 'totalReplyCount': 3}, 'replies': {'comments': [comment('r1')]}}
        calls = []

        class API:

            def commentThreads(self):
                return SimpleNamespace(list=lambda **kw: SimpleNamespace(execute=lambda: {'items': [top]}))

            def comments(self):

                def query(**kw):
                    calls.append(kw)
                    data = {'items': [comment('r1'), comment('r2')], 'nextPageToken': 'next'} if kw['pageToken'] is None else {'items': [comment('r3')]}
                    return SimpleNamespace(execute=lambda: data)
                return SimpleNamespace(list=query)
        self.n['HttpError'] = RuntimeError
        raw = self.n['yt_fetch']('video', 4, yt=API())
        self.assertEqual(len(raw), 4)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all((c['parentId'] == 'top' for c in calls)))
        norm = [self.n['yt_normalise'](r) for r in raw]
        self.assertEqual(norm[0]['post_id'], 'top')
        self.assertTrue(all((r['thread_id'] == 'top' for r in norm)))
        self.assertEqual(len(self.n['yt_fetch']('video', 1, yt=API())), 1)

    def test_youtube_batch_skips_unavailable_but_stops_safely_on_quota(self):
        n = load_notebook()

        class FakeHttpError(Exception):
            def __init__(self, reason):
                super().__init__('request URL containing SECRET_KEY')
                self.content = json.dumps({'error': {'errors': [{'reason': reason}]}}).encode()
                self.resp = SimpleNamespace(status=404 if reason == 'videoNotFound' else 403)

        def request(items=None, error=None):
            def execute():
                if error:
                    raise FakeHttpError(error)
                return {'items': items or []}
            return SimpleNamespace(execute=execute)

        def top(cid, replies=0):
            return {'snippet': {'topLevelComment': {'id': cid, 'snippet': {'likeCount': 2}},
                                'totalReplyCount': replies}}

        class API:
            reply_error = 'commentsDisabled'

            def commentThreads(self):
                def query(**kw):
                    vid = kw['videoId']
                    if vid == 'missing':
                        return request(error='videoNotFound')
                    return request([top(vid, replies=1 if vid == 'reply_failure' else 0)])
                return SimpleNamespace(list=query)

            def comments(self):
                return SimpleNamespace(list=lambda **kw: request(error=self.reply_error))

        api = API()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            n.update(HttpError=FakeHttpError, build=lambda *a, **kw: api,
                     YOUTUBE_API_KEY='unused', YT_MAX_PER_VIDEO=5,
                     YT_VIDEO_IDS=['first', 'missing', 'reply_failure', 'last'],
                     RAW=path, CANONICAL=path)
            result = n['run_youtube_collection']()
            self.assertEqual(result.post_id.tolist(), ['first', 'last'])
            csv = path / 'youtube.csv'
            previous = csv.read_bytes()
            self.assertEqual(list(n['load_canonical']('youtube').columns),
                             ['like_count', 'reply_count', 'date_published'])
            n['YT_VIDEO_IDS'] = ['missing']
            self.assertIsNone(n['run_youtube_collection']())
            self.assertEqual(csv.read_bytes(), previous)
            api.reply_error = 'quotaExceeded'
            n['YT_VIDEO_IDS'] = ['reply_failure', 'last']
            try:
                n['run_youtube_collection']()
            except RuntimeError:
                self.assertNotIn('SECRET_KEY', traceback.format_exc())
            else:
                self.fail('Quota errors must stop the batch')
            self.assertEqual(csv.read_bytes(), previous)

    def test_browser_canonical_hard_cap_and_profile_exclusion(self):
        with tempfile.TemporaryDirectory() as folder:
            old = {k: self.n.get(k) for k in ['RAW', 'CANONICAL']}
            self.n.update(RAW=Path(folder), CANONICAL=Path(folder))
            try:
                rows = [dict(_capture_session='run', _max_comments=1, _page_url='https://tiktok.com/@u/video/1', _kind='comment', payload={'comments': [dict(cid=str(i), text='text', user={'uid': str(i + 10)}) for i in range(1, 4)]})]
                rows.append(dict(_kind='profile', payload={'comments': [dict(cid='profile', text='ignore', user={'uid': '99'})]}))
                self.n['jsonl_append'](Path(folder) / 'tiktok_payloads.jsonl', rows)
                d = self.n['build_browser_canonical']('tiktok')
                self.assertEqual(len(d), 1)
            finally:
                self.n.update(old)

    def test_parse_failure_preserves_existing_canonical(self):
        with tempfile.TemporaryDirectory() as folder:
            old = {k: self.n.get(k) for k in ['RAW', 'CANONICAL']}
            self.n.update(RAW=Path(folder), CANONICAL=Path(folder))
            try:
                canonical = Path(folder) / 'tiktok.csv'
                canonical.write_text('previous successful dataset', encoding='utf-8')
                self.n['jsonl_append'](Path(folder) / 'tiktok_payloads.jsonl', [{'payload': {'comments': [7]}}])
                with self.assertRaises(ValueError):
                    self.n['build_browser_canonical']('tiktok')
                self.assertEqual(canonical.read_text(encoding='utf-8'), 'previous successful dataset')
            finally:
                self.n.update(old)
if __name__ == '__main__':
    unittest.main()
