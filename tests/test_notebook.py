import json
import os
import re
import tempfile
import unittest
import traceback
from unittest.mock import patch, Mock
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
from notebook_loader import load_notebook

class PipelineTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.n = load_notebook()

    def record(self, user='u', comment='c', platform='youtube', source='video1', **extra):
        values = dict(username='00123', comment_text='NA, quoted "text"\nsecond line', user_id=user, post_id=comment, _platform=platform, source_post_id=source, thread_id=comment, text_content='a sufficiently long message', clean_text='message', _collected_at='2026-01-02T00:00:00Z', created_at='2026-01-01T00:00:00Z', is_reply=False, media_types=['text'])
        values.update(extra)
        return self.n['empty_record'](**values)

    def test_full_comment_schema_export_all_platforms(self):
        with tempfile.TemporaryDirectory() as folder:
            old = {k: self.n.get(k) for k in ['CANONICAL', 'FEATURES']}
            self.n.update(CANONICAL=Path(folder), FEATURES=Path(folder))
            try:
                for platform in ('youtube', 'tiktok', 'instagram', 'x', 'facebook'):
                    frame = pd.DataFrame([self.record(platform=platform, like_count=0, reply_count=None, created_at='2026-01-01T07:00:00+07:00'), self.record(platform=platform, comment='d', like_count=12, reply_count=3, created_at=None)])
                    self.n['save_canonical'](frame, platform)
                    out = self.n['load_canonical'](platform)
                    self.assertEqual(list(out.columns), ['platform', 'video_id', 'comment_id', 'parent_comment_id', 'username', 'comment_text', 'post_text', 'like_count', 'reply_count', 'date_published', 'bias_label'])
                    self.assertEqual(out.username.iloc[0], "00123")
                    self.assertEqual(out.comment_text.iloc[0], 'NA, quoted "text"\nsecond line')
                    self.assertEqual(out.like_count.iloc[0], 0)
                    self.assertTrue(pd.isna(out.reply_count.iloc[0]))
                    self.assertEqual(pd.Timestamp(out.date_published.iloc[0]), pd.Timestamp('2026-01-01T00:00:00Z'))
                    self.assertTrue(pd.isna(out.date_published.iloc[1]))
                combined = self.n['assemble_scraped_data']()
                self.assertEqual(len(combined), 10)
                self.assertEqual(list(combined.columns), ['platform', 'video_id', 'comment_id', 'parent_comment_id', 'username', 'comment_text', 'post_text', 'like_count', 'reply_count', 'date_published', 'bias_label'])
                self.assertFalse((Path(folder) / 'dataset_accounts.csv').exists())
            finally:
                self.n.update(old)

    def test_author_and_text_survive_all_platform_parsers(self):
        n = self.n
        rows = [
            n['parse_tiktok']({'payload': {'comments': [{'cid': '1', 'text': 'hello', 'user': {'unique_id': 'alice'}}]}})[0],
            n['parse_instagram']({'payload': {'comments': [{'pk': '1', 'text': 'hello', 'user': {'username': 'alice'}}]}})[0],
            n['parse_x']({'_page_url': 'https://x.com/u/status/1', 'payload': {
                'rest_id': '2', 'legacy': {'full_text': 'truncated', 'conversation_id_str': '1'},
                'note_tweet': {'note_tweet_results': {'result': {'text': 'hello'}}},
                'core': {'user_results': {'result': {'core': {'screen_name': 'alice'}}}}}})[0],
            n['parse_facebook']({'payload': {'__typename': 'Comment', 'id': '1',
                'body': {'text': 'hello'}, 'author': {'name': 'alice'}}})[0],
            n['yt_normalise']({'_kind': 'reply', '_video_id': '1', 'item': {'id': '2',
                'snippet': {'textOriginal': 'hello', 'authorDisplayName': 'alice'}}}),
        ]
        for row in rows:
            exported = n['scraping_columns'](n['to_frame']([row]))
            self.assertEqual(exported.username.iloc[0], 'alice')
            self.assertEqual(exported.comment_text.iloc[0], 'hello')
        missing = n['parse_instagram']({'payload': {'comments': [{'pk': '1', 'text': 'hello'}]}})[0]
        self.assertIsNone(missing['username'])

    def test_env_login_success_fallback_and_secret_redaction(self):
        credentials = dict(INSTAGRAM_USERNAME='dummy', INSTAGRAM_PASSWORD='secret-test',
                           FACEBOOK_EMAIL='dummy@example.com', FACEBOOK_PASSWORD='secret-test',
                           X_USERNAME='dummy', X_PASSWORD='secret-test')
        with patch.dict(os.environ, credentials, clear=True):
            for platform in ('instagram', 'facebook', 'x'):
                h = self.n['Harvester'](platform)
                h.page = Mock()
                h.page.locator.return_value.first.is_visible.return_value = False
                h.page.goto.side_effect = lambda url, **kw: setattr(h.page, 'url', url)
                self.assertTrue(h.login_from_env())
                self.assertEqual(h.page.locator.return_value.first.fill.call_count, 2)
                self.assertFalse(h._logging_in)
                h.page.locator.return_value.first.fill.side_effect = RuntimeError('secret-test')
                with patch('builtins.print') as output:
                    self.assertFalse(h.login_from_env())
                self.assertNotIn('secret-test', str(output.call_args_list))
                self.assertFalse(h._logging_in)
                h.page.locator.return_value.first.fill.reset_mock(side_effect=True)
                h.page.goto.side_effect = lambda url, **kw: setattr(h.page, 'url', 'https://unrelated.example/login')
                self.assertFalse(h.login_from_env())
                h.page.locator.return_value.first.fill.assert_not_called()
                h.page.locator.return_value.first.is_visible.return_value = True
                h.page.goto.reset_mock()
                self.assertTrue(h.login_from_env())
                h.page.goto.assert_not_called()
        with patch.dict(os.environ, {}, clear=True):
            h.page.locator.return_value.first.is_visible.return_value = False
            self.assertFalse(h.login_from_env())
            h.page.goto.assert_not_called()
        h._logging_in = True
        h._on_response(SimpleNamespace())
        self.assertEqual(h.captured, [])

    def test_instagram_reply_mentions_do_not_replace_missing_counts(self):
        rows = [self.n['empty_record'](post_id='a', thread_id='top', parent_comment_id='top',
                    username='alice', comment_text='hello'),
                self.n['empty_record'](post_id='b', thread_id='top', parent_comment_id='top',
                    username='bob', comment_text='@alice yes', reply_count=0)]
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(self.n, CANONICAL=Path(folder)):
                path = self.n['save_canonical'](self.n['to_frame'](rows), 'instagram')
                restored = self.n['load_canonical']('instagram')
                self.assertTrue(pd.isna(restored.reply_count.iloc[0]))
                self.assertEqual(restored.reply_count.iloc[1], 0)
                self.assertIn('\\N', path.read_text())

    def test_context_and_ids_survive_csv_without_invented_labels(self):
        n = self.n
        long_id = '001234567890123456789'
        records = [n['empty_record'](post_id=long_id, source_post_id='000123',
                    parent_comment_id='000456', username='alice', comment_text='a reply',
                    post_text='Caption, with\na newline', reply_count=None)]
        with tempfile.TemporaryDirectory() as folder, patch.dict(n, CANONICAL=Path(folder), FEATURES=Path(folder)):
            n['save_canonical'](n['to_frame'](records), 'instagram')
            out = n['assemble_scraped_data'](('instagram',))
            self.assertEqual(out.comment_id.iloc[0], long_id)
            self.assertEqual(out.video_id.iloc[0], '000123')
            self.assertEqual(out.parent_comment_id.iloc[0], '000456')
            self.assertEqual(out.platform.iloc[0], 'instagram')
            self.assertEqual(out.post_text.iloc[0], 'Caption, with\na newline')
            self.assertTrue(pd.isna(out.bias_label.iloc[0]))
            self.assertTrue(pd.isna(out.reply_count.iloc[0]))
            out.loc[0, 'bias_label'] = 'reviewed'
            self.assertEqual(n['scraping_columns'](out).bias_label.iloc[0], 'reviewed')
        for platform in ('instagram', 'facebook'):
            payload = {'comments': [{'pk': 'c', 'text': 'hello'}]} if platform == 'instagram' else {
                '__typename': 'Comment', 'id': 'c', 'body': {'text': 'hello'}, 'author': {}}
            url = 'https://instagram.com/p/SHORT/' if platform == 'instagram' else 'https://facebook.com/reel/123/'
            parsed = n['parse_' + platform]({'payload': payload, '_page_url': url, '_post_text': 'Original caption'})[0]
            self.assertEqual(parsed['source_post_id'], 'SHORT' if platform == 'instagram' else '123')
            self.assertEqual(parsed['post_text'], 'Original caption')

    def test_media_comments_and_only_media_parent_threads_are_excluded(self):
        n = self.n
        make = n['empty_record']
        rows = [make(post_id='root', source_post_id='v', _has_media=True, comment_text='Text with GIF'),
                make(post_id='reply', source_post_id='v', parent_comment_id='root'),
                make(post_id='nested', source_post_id='v', parent_comment_id='reply'),
                make(post_id='text', source_post_id='v', comment_text='hello'),
                make(post_id='gifreply', source_post_id='v', parent_comment_id='text', _has_media=True),
                make(post_id='keep', source_post_id='v', parent_comment_id='gifreply', comment_text='text reply'),
                make(post_id='root', source_post_id='other', comment_text='another video'),
                make(post_id='blank', source_post_id='v', comment_text='   ')]
        out = n['to_frame'](list(reversed(rows)))
        self.assertEqual(set(zip(out.source_post_id, out.post_id)), {('v', 'text'), ('v', 'keep'), ('other', 'root')})
        self.assertNotIn('_has_media', n['scraping_columns'](out).columns)
        for platform, payload in [
            ('instagram', {'comments': [{'pk': 'root', 'giphy_media_info': {'id': 'gif'},
                'preview_child_comments': [{'pk': 'reply', 'text': 'reply'}]}]}),
            ('tiktok', {'comments': [{'cid': 'root', 'text': '', 'image_list': [{'url': 'image'}],
                'reply_comment': [{'cid': 'reply', 'text': 'reply'}]}]}),
            ('facebook', {'__typename': 'Comment', 'id': 'root', 'author': {}, 'attachments': [{'media': {}}],
                'replies': [{'__typename': 'Comment', 'id': 'reply', 'body': {'text': 'reply'},
                    'author': {}, 'parent_comment': {'id': 'root'}}]}),
        ]:
            parsed = n['parse_' + platform]({'payload': payload})
            self.assertEqual(len(parsed), 2)
            self.assertTrue(n['to_frame'](parsed).empty, platform)
        self.assertFalse(n['comment_has_media']({'text': 'I like GIFs 😀',
            'user': {'profile_pic_url': 'avatar'}, 'giphy_media_info': None}))
        self.assertTrue(n['comment_has_media']({'extended_entities': {'media': [{'type': 'animated_gif'}]}}))

    def test_tiktok_late_caption_is_archived_and_shared_only_with_same_video(self):
        n = load_notebook()
        h = n['Harvester']('tiktok')
        h.target_url = 'https://www.tiktok.com/@u/video/123'
        h.page = Mock()
        h._read_post_text = Mock(return_value='Actual caption #news')
        row = {'_platform': 'tiktok', '_page_url': h.target_url,
               'payload': {'comments': [{'cid': 'new', 'text': 'new comment'}]}}
        h.captured = [row]
        with tempfile.TemporaryDirectory() as folder, patch.dict(n, RAW=Path(folder), CANONICAL=Path(folder)):
            older = {'_platform': 'tiktok', '_page_url': h.target_url,
                     'payload': {'comments': [{'cid': 'old', 'text': 'old comment'}]}}
            other = {'_platform': 'tiktok', '_page_url': 'https://www.tiktok.com/@u/video/456',
                     'payload': {'comments': [{'cid': 'other', 'text': 'other comment'}]}}
            n['jsonl_append'](Path(folder) / 'tiktok_payloads.jsonl', [older, other])
            h.flush()
            out = n['build_browser_canonical']('tiktok').set_index('post_id')
            self.assertEqual(out.loc['old', 'post_text'], 'Actual caption #news')
            self.assertEqual(out.loc['new', 'post_text'], 'Actual caption #news')
            self.assertTrue(pd.isna(out.loc['other', 'post_text']))

    def test_tiktok_reply_pagination_excludes_hide_and_stops_on_blocked_threads(self):
        n = self.n
        pattern = n['REPLY_BUTTON_PATTERNS']['tiktok']
        for label in ('View 68 replies', 'View 65 more replies', 'View more replies (65)',
                      'Lihat 65 balasan lainnya', 'Lihat balasan (68)', 'Lihat 32 lainnya', 'Lihat 1 lainnya'):
            self.assertIsNotNone(re.search(pattern, label, re.I))
        for label in ('Hide replies', 'Sembunyikan balasan', 'Sembunyikan', 'Reply', 'Balas', 'Jawab'):
            self.assertIsNone(re.search(pattern, label, re.I))
        h = n['Harvester']('tiktok')
        h.page = Mock()
        controls = h.page.locator.return_value.filter.return_value.locator.return_value
        controls.count.return_value = 1
        h._drain_reply_thread = Mock(side_effect=RuntimeError('stale control'))
        with self.assertRaisesRegex(RuntimeError, 'collection stopped'):
            h.expand_replies(max_clicks=5)
        h.page.mouse.wheel.assert_not_called()
        h._drain_reply_thread = Mock(return_value=25)
        controls.count.side_effect = [1, 0, 0, 0]
        h._at_cap = Mock(return_value=True)
        self.assertEqual(h.expand_replies(max_clicks=1000), 25)
        rows = [n['empty_record'](post_id='root', source_post_id='v', thread_id='root', reply_count=68, comment_text='root text'),
                n['empty_record'](post_id='a', source_post_id='v', thread_id='root', parent_comment_id='root', comment_text='reply'),
                n['empty_record'](post_id='b', source_post_id='v', thread_id='root', parent_comment_id='a', comment_text='nested reply')]
        with patch('builtins.print'):
            gaps = n['report_tiktok_reply_gaps'](n['to_frame'](rows))
        self.assertEqual(gaps, [('root', 2, 68)])
        self.assertEqual(rows[0]['reply_count'], 68)

    def test_every_cell_compiles(self):
        nb = json.loads((Path(__file__).resolve().parents[1] / 'buzzer.ipynb').read_text(encoding='utf-8'))
        for i, cell in enumerate(nb['cells']):
            if cell['cell_type'] == 'code':
                compile(''.join(cell['source']), f'cell-{i}', 'exec')

    def test_all_video_lists_and_no_account_scrapers(self):
        configured = self.n['VIDEO_URLS']
        self.assertEqual(set(configured), {'tiktok', 'x', 'instagram', 'youtube', 'facebook'})
        urls = [url for group in self.n['VIDEO_URLS'].values() for url in group]
        self.assertEqual(len(set(urls)), len(urls))
        self.assertEqual({(t['platform'], t['url']) for t in self.n['BROWSER_TARGETS']},
                         {(p, url) for p, group in configured.items() if p != 'youtube' for url in group})
        self.assertEqual(self.n['YT_VIDEO_IDS'],
                         [self.n['source_post_id']('youtube', url) for url in configured['youtube']])
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

    def test_instagram_graphql_capture_coverage_and_export(self):
        reply = {'id': '2', 'text': 'reply', 'owner': {'username': 'bob'}, 'edge_liked_by': {'count': 0}}
        comment = {'id': '1', 'text': 'hello', 'owner': {'username': 'alice'},
                   'created_at': 1700000000, 'edge_liked_by': {'count': 3},
                   'edge_threaded_comments': {'count': 1, 'edges': [{'node': reply}]}}
        payload = {'data': {'shortcode_media': {
            'edge_media_to_caption': {'edges': [{'node': {'id': 'caption', 'text': 'not a comment'}}]},
            'edge_media_to_parent_comment': {'edges': [{'node': comment}]}}}}
        h = self.n['Harvester']('instagram')
        h.target_url = 'https://www.instagram.com/p/POST/'
        response = SimpleNamespace(url='https://www.instagram.com/graphql/query', status=200,
            headers={'content-type': 'application/json'}, text=lambda: json.dumps(payload))
        h._on_response(response)
        self.assertEqual(h._coverage_snapshot()[0], 2)
        records = self.n['parse_instagram'](h.captured[0])
        self.assertEqual([r['post_id'] for r in records], ['1', '2'])
        self.assertEqual(records[1]['parent_comment_id'], '1')
        exported = self.n['scraping_columns'](self.n['to_frame'](records))
        self.assertEqual(exported.username.tolist(), ['alice', 'bob'])
        self.assertEqual(exported.comment_text.tolist(), ['hello', 'reply'])
        self.assertEqual(exported.like_count.tolist(), [3, 0])
        payload = {'data': {'xdt_api__v1__media__media_id__comments__connection': {
            'edges': [{'node': {'pk': '3', 'text': 'modern', 'user': {'username': 'carol'}}}]}}}
        h._on_response(response)
        self.assertEqual(h._coverage_snapshot()[0], 3)
        payload = {'data': {'viewer': {'id': 'user', 'username': 'not a comment'}}}
        h._on_response(response)
        self.assertEqual(len(h.captured), 2)

    def test_instagram_visible_panel_triggers_capture_before_prompt(self):
        h = SimpleNamespace(platform='instagram', page=Mock(),
                            _coverage_snapshot=Mock(return_value=(0, None)),
                            _scroll_instagram_comments=Mock(return_value=True))
        with ThreadPoolExecutor(max_workers=1) as ex, patch('builtins.input', side_effect=AssertionError('unnecessary login prompt')):
            self.assertTrue(self.n['wait_for_comment_access'](ex, h))
        h._scroll_instagram_comments.assert_called_once_with(required=False)

    def test_browser_login_preserves_comments_on_current_target(self):
        n = load_notebook()
        target = 'https://www.instagram.com/p/POST/'
        h = Mock()
        h.start.return_value = h
        h.page.url = target
        h.login_from_env.return_value = False
        h._seen_ids = {'1'}
        h.capture_session = 'session'
        h.stop_reason = 'cap'
        h.scroll_until_coverage.return_value = (1, None)
        n.update(Harvester=Mock(return_value=h), ThreadPoolExecutor=ThreadPoolExecutor,
                 wait_for_comment_access=Mock(return_value=True), jsonl_append=Mock(),
                 RAW=Path('.'), SESSION_SEEN={})
        with patch('builtins.input', return_value=''):
            n['run_browser_collection']([{'platform': 'instagram', 'url': target}])
        h.open.assert_called_once_with(target, 500)
        h.open.reset_mock()
        h.page.url = 'https://www.instagram.com/'
        with patch('builtins.input', return_value=''):
            n['run_browser_collection']([{'platform': 'instagram', 'url': target}])
        self.assertEqual(h.open.call_count, 2)

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
        h.page = SimpleNamespace(goto=lambda *a, **kw: None, wait_for_timeout=lambda *a: None, evaluate=lambda *a: "Post caption")

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

    def test_browser_access_requires_comments_or_explicit_skip(self):
        n = load_notebook()
        h = SimpleNamespace(page=SimpleNamespace(wait_for_timeout=lambda ms: None),
                            _coverage_snapshot=lambda: (0, None))
        with ThreadPoolExecutor(max_workers=1) as ex:
            with patch('builtins.input', return_value='s'):
                self.assertFalse(n['wait_for_comment_access'](ex, h))
                self.assertIn('skipped', h.stop_reason)
            def login_and_open_comments(prompt):
                h._coverage_snapshot = lambda: (2, None)
                return ''
            with patch('builtins.input', side_effect=login_and_open_comments) as prompt:
                self.assertTrue(n['wait_for_comment_access'](ex, h))
                prompt.assert_called_once()
            with patch('builtins.input', side_effect=AssertionError('unnecessary prompt')):
                self.assertTrue(n['wait_for_comment_access'](ex, h))

    def test_instagram_expands_replies_before_scrolling_and_cap(self):
        pattern = self.n['REPLY_BUTTON_PATTERNS']['instagram']
        for label in ('Lihat semua 3 balasan', 'Lihat balasan', 'View replies (3)', 'View all 12 replies'):
            self.assertIsNotNone(re.search(pattern, label, re.I))
        for label in ('Balas', 'Reply', 'Hide replies', 'Sembunyikan balasan'):
            self.assertIsNone(re.search(pattern, label, re.I))
        h = self.n['Harvester']('instagram')
        events = []
        h._at_cap = Mock(side_effect=[False, True])
        h.expand_replies = Mock(side_effect=lambda **kw: events.append('replies'))
        h._scroll_instagram_comments = Mock(side_effect=lambda: events.append('scroll'))
        h.scroll_comments(max_rounds=1, pause_ms=0, expand_replies=True)
        self.assertEqual(events, ['replies'])
        h._at_cap = Mock(return_value=False)
        h._wait_for_growth = Mock()
        h.scroll_comments(max_rounds=1, pause_ms=0, expand_replies=True)
        self.assertEqual(events, ['replies', 'replies', 'scroll'])

    def test_instagram_scroll_never_uses_feed_wheel(self):
        h = self.n['Harvester']('instagram')
        h.page = Mock()
        h.page.evaluate.return_value = True
        h.scroll_comments(max_rounds=2, pause_ms=0)
        self.assertEqual(h.page.evaluate.call_count, 2)
        h.page.mouse.wheel.assert_not_called()
        h.page.mouse.move.assert_not_called()
        h.page.evaluate.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'comment panel not found'):
            h.scroll_comments(max_rounds=1, pause_ms=0)
        h.page.mouse.wheel.assert_not_called()
        h.platform = 'x'
        h.page.viewport_size = {'width': 1440, 'height': 900}
        h.scroll_comments(max_rounds=1, pause_ms=0)
        h.page.mouse.wheel.assert_called_once_with(0, 3200)

    def test_youtube_reply_pagination_and_comment_id(self):

        def comment(cid):
            return {'id': cid, 'snippet': {'textOriginal': 'test', 'parentId': 'top'}}
        top = {'id': 'THREAD_RESOURCE_ID', 'snippet': {'topLevelComment': comment('top'), 'totalReplyCount': 3}, 'replies': {'comments': [comment('r1')]}}
        calls = []

        class API:

            def videos(self):
                return SimpleNamespace(list=lambda **kw: SimpleNamespace(execute=lambda: {
                    'items': [{'snippet': {'title': 'Post title', 'description': 'Post description'}}]}))

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
        self.assertEqual(norm[0]['post_text'], 'Post title\n\nPost description')
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
            return {'snippet': {'topLevelComment': {'id': cid, 'snippet': {'likeCount': 2, 'textOriginal': 'text comment'}},
                                'totalReplyCount': replies}}

        class API:
            reply_error = 'commentsDisabled'

            def videos(self):
                return SimpleNamespace(list=lambda **kw: SimpleNamespace(execute=lambda: {
                    'items': [{'snippet': {'title': 'Post title', 'description': 'Post description'}}]}))

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
                             ['platform', 'video_id', 'comment_id', 'parent_comment_id', 'username', 'comment_text', 'post_text', 'like_count', 'reply_count', 'date_published', 'bias_label'])
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
                rows[0]['_complete_reply_threads'] = True
                for comment in rows[0]['payload']['comments'][1:]:
                    comment['reply_id'] = '1'
                self.n['jsonl_append'](Path(folder) / 'tiktok_payloads.jsonl', rows[:1])
                d = self.n['build_browser_canonical']('tiktok')
                self.assertEqual(len(d), 3)
                self.assertEqual(d.parent_comment_id.notna().sum(), 2)
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
