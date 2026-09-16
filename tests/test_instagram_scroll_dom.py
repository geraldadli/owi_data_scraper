"""Browser regression for Instagram's standalone post layout (no article/dialog)."""
import ast
import json
import os
from types import SimpleNamespace
from pathlib import Path
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, 'Playwright is not installed in this interpreter')
class InstagramScrollTests(unittest.TestCase):
    def test_reply_thread_finishes_delayed_pages_before_next_parent(self):
        nb = json.loads((Path(__file__).resolve().parents[1] / 'buzzer.ipynb').read_text(encoding='utf-8'))
        source = next(''.join(c['source']) for c in nb['cells'] if 'class Harvester' in ''.join(c['source']))
        tree = ast.parse(source)
        methods = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                   and node.name in {'expand_replies', '_drain_reply_thread'}]
        assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == 'REPLY_BUTTON_PATTERNS' for t in node.targets))
        ns = {'REPLY_BUTTON_PATTERNS': ast.literal_eval(assignment.value)}
        exec(compile(ast.Module(body=methods, type_ignores=[]), 'reply_methods', 'exec'), ns)
        Collector = type('Collector', (), {name: ns[name] for name in ('expand_replies', '_drain_reply_thread')})
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_TEST_EXECUTABLE'))
            try:
                page = browser.new_page()
                for platform in ('tiktok', 'instagram'):
                    page.set_content('''<div style="height:300px;overflow:auto">
                      <div id="a"><a href="/alice/">Alice</a><button onclick="window.order.push('hide')">Sembunyikan</button><button onclick="more(this)">View replies (68)</button></div>
                      <div id="b"><a href="/bob/">Bob</a><button onclick="window.order.push('b');this.remove()">View replies (1)</button></div>
                      <script>window.order=[];window.pages=0;
                        function more(button){window.order.push('a');window.pages++;button.remove();
                          if(window.pages<25)setTimeout(()=>{const b=document.createElement('button');
                            b.textContent=window.useIndonesian ? 'Lihat '+(26-window.pages)+' lainnya' : 'View more replies';b.onclick=()=>more(b);document.querySelector('#a').append(b)},15)}
                      </script></div>''')
                    page.evaluate("value => window.useIndonesian = value", platform == "tiktok")
                    h = Collector()
                    h.platform = platform
                    h.page = SimpleNamespace(locator=page.locator, wait_for_timeout=lambda ms: page.wait_for_timeout(10))
                    h._wait_for_growth = lambda ms: None
                    h._at_cap = lambda: True
                    self.assertEqual(h.expand_replies(max_clicks=1000), 26)
                    self.assertEqual(page.evaluate('window.order'), ['a'] * 25 + ['b'])
            finally:
                browser.close()

    def test_tiktok_caption_uses_selected_video_data_and_visible_fallback(self):
        nb = json.loads((Path(__file__).resolve().parents[1] / 'buzzer.ipynb').read_text(encoding='utf-8'))
        source = next(''.join(c['source']) for c in nb['cells'] if 'class Harvester' in ''.join(c['source']))
        method = next(node for node in ast.walk(ast.parse(source))
                      if isinstance(node, ast.FunctionDef) and node.name == '_read_post_text')
        script = next(node.args[0].value for node in ast.walk(method)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                      and node.func.attr == 'evaluate' and len(node.args) == 2)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_TEST_EXECUTABLE'))
            try:
                page = browser.new_page()
                page.route('**/*', lambda route: route.fulfill(body='<html></html>', content_type='text/html'))
                page.goto('https://www.tiktok.com/@test/video/123')
                caption = 'Full caption #news\nSecond line'
                for script_id, data in [
                    ('__UNIVERSAL_DATA_FOR_REHYDRATION__', {'__DEFAULT_SCOPE__': {'webapp.video-detail': {
                        'itemInfo': {'itemStruct': {'id': '123', 'desc': caption}}}}}),
                    ('SIGI_STATE', {'ItemModule': {'123': {'id': '123', 'desc': caption},
                                                 '456': {'id': '456', 'desc': 'unrelated'}}}),
                ]:
                    page.set_content(f'<script id="{script_id}" type="application/json">{json.dumps(data)}</script>')
                    self.assertEqual(page.evaluate(script, '123'), caption)
                    self.assertIsNone(page.evaluate(script, '456'))
                page.set_content('<script id="SIGI_STATE">broken JSON</script><div data-e2e="browse-video-desc">Visible caption #news</div>')
                self.assertEqual(page.evaluate(script, '123'), 'Visible caption #news')
                page.set_content('<div data-e2e="video-desc">first</div><div data-e2e="video-desc">second</div>')
                self.assertIsNone(page.evaluate(script, '123'))
            finally:
                browser.close()

    def test_standalone_comment_panel_never_scrolls_outer_feed(self):
        nb = json.loads((Path(__file__).resolve().parents[1] / 'buzzer.ipynb').read_text(encoding='utf-8'))
        source = next(''.join(c['source']) for c in nb['cells'] if 'class Harvester' in ''.join(c['source']))
        method = next(node for node in ast.walk(ast.parse(source))
                      if isinstance(node, ast.FunctionDef) and node.name == '_scroll_instagram_comments')
        script = next(node.args[0].value for node in ast.walk(method)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                      and node.func.attr == 'evaluate')
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_TEST_EXECUTABLE'))
            try:
                page = browser.new_page(viewport={'width': 1440, 'height': 900})
                page.set_content('''<div id="feed" style="height:800px;overflow:auto">
                  <div style="display:flex;margin-left:200px">
                    <video style="width:400px;height:700px"></video>
                    <div id="comments" style="width:400px;height:500px;overflow-y:auto">
                      <a href="/someone/">someone</a><p>Comment text</p><button>Balas</button>
                      <div style="height:1800px"></div>
                    </div>
                  </div><div style="height:2500px"></div></div>''')
                self.assertTrue(page.evaluate(script))
                self.assertGreater(page.locator('#comments').evaluate('(e) => e.scrollTop'), 0)
                self.assertEqual(page.locator('#feed').evaluate('(e) => e.scrollTop'), 0)
                page.locator('#comments').evaluate('(e) => e.scrollTop = e.scrollHeight')
                self.assertTrue(page.evaluate(script))
                self.assertEqual(page.locator('#feed').evaluate('(e) => e.scrollTop'), 0)
                page.locator('#comments').evaluate('(e) => e.remove()')
                self.assertFalse(page.evaluate(script))
                self.assertEqual(page.locator('#feed').evaluate('(e) => e.scrollTop'), 0)
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
