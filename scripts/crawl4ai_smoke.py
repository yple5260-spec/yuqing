"""Check browser rendering, extraction and screenshot without accessing business data."""
import asyncio
import base64
import io
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from PIL import Image

OUTPUT = Path(__file__).resolve().parents[1] / 'runtime' / 'crawl4ai-smoke'


class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'''<!doctype html><html><head><meta charset="utf-8"><title>Collector check</title></head>
        <body><h1>Collector environment check</h1><main id="result">Loading</main>
        <script>setTimeout(() => document.querySelector('#result').textContent =
        'crawl4ai-smoke-ready: browser rendering works', 100);</script></body></html>'''
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


async def verify(url):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    os.chdir(OUTPUT)
    async with AsyncWebCrawler(config=BrowserConfig(headless=True), base_directory=str(OUTPUT)) as crawler:
        result = await crawler.arun(url=url, config=CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS, screenshot=True, word_count_threshold=0,
            wait_for="js:() => document.querySelector('#result')?.textContent.includes('crawl4ai-smoke-ready')"))
    if not result.success or result.status_code != 200:
        raise RuntimeError('Browser check failed; inspect local crawler diagnostics')
    markdown = result.markdown.raw_markdown
    if 'crawl4ai-smoke-ready' not in markdown or not result.screenshot:
        raise RuntimeError('Rendered content or screenshot missing')
    image = base64.b64decode(result.screenshot, validate=True)
    with Image.open(io.BytesIO(image)) as screenshot:
        screenshot.verify()
    (OUTPUT / 'page.md').write_text(markdown, encoding='utf-8')
    (OUTPUT / 'screenshot.png').write_bytes(image)
    report = {'rendering': 'PASS', 'extraction': 'PASS', 'screenshot': 'PASS',
              'business_database_accessed': False, 'target_platform_tested': False}
    (OUTPUT / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        asyncio.run(verify('http://127.0.0.1:' + str(server.server_port)))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
