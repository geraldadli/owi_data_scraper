"""Browser regression for Instagram's standalone post layout (no article/dialog)."""
import ast
import json
import os
from pathlib import Path
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, 'Playwright is not installed in this interpreter')
class InstagramScrollTests(unittest.TestCase):
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
