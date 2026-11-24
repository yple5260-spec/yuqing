"""Install Playwright's pinned Linux headless Chromium using parallel HTTPS ranges."""
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import base64
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import URLError
from urllib.request import Request, urlopen

CHUNK_SIZE = 2 * 1024 * 1024
INSTALL = [sys.executable, '-m', 'playwright', 'install', '--only-shell', 'chromium']


def download(url, archive):
    with urlopen(Request(url, method='HEAD'), timeout=30) as response:
        size = int(response.headers['Content-Length'])
        hashes = ','.join(response.headers.get_all('x-goog-hash', []))
    digest = re.search(r'\bmd5=([A-Za-z0-9+/=]+)', hashes)
    if not digest or not 0 < size <= 512 * 1024 * 1024:
        raise ValueError('Chromium object metadata is incomplete')
    with archive.open('wb') as output:
        output.truncate(size)

    def fetch(start):
        end = min(start + CHUNK_SIZE, size) - 1
        request = Request(url, headers={'Range': f'bytes={start}-{end}'})
        for attempt in range(3):
            try:
                with urlopen(request, timeout=30) as response:
                    if response.status != 206 or response.headers['Content-Range'] != f'bytes {start}-{end}/{size}':
                        raise ValueError('Unexpected Chromium byte range')
                    data = response.read(end - start + 2)
                if len(data) != end - start + 1:
                    raise ValueError('Truncated Chromium byte range')
                break
            except (URLError, OSError, ValueError):
                if attempt == 2:
                    raise
        with archive.open('r+b') as output:
            output.seek(start)
            output.write(data)

    with ThreadPoolExecutor(max_workers=12) as pool:
        for _ in pool.map(fetch, range(0, size, CHUNK_SIZE)):
            pass
    with archive.open('rb') as stream:
        checksum = hashlib.file_digest(stream, lambda: hashlib.md5(usedforsecurity=False))
    if base64.b64encode(checksum.digest()).decode() != digest.group(1):
        raise ValueError('Chromium download checksum does not match Google Cloud Storage')
    print('Chromium archive download and checksum verified', flush=True)


def main():
    plan = subprocess.check_output([*INSTALL, '--dry-run'], text=True)
    artifact = re.search(r'https://cdn\.playwright\.dev/builds/cft/([\d.]+/linux64/chrome-headless-shell-linux64\.zip)', plan)
    if not artifact:
        raise RuntimeError('Playwright did not resolve the Linux headless Chromium download')
    relative = artifact.group(1)
    with TemporaryDirectory() as directory:
        archive = Path(directory) / 'builds/cft' / relative
        archive.parent.mkdir(parents=True)
        print(f'Fetching the pinned Chromium archive for {relative.split("/")[0]}', flush=True)
        download('https://storage.googleapis.com/chrome-for-testing-public/' + relative, archive)
        with ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=directory)) as mirror:
            worker = Thread(target=mirror.serve_forever, daemon=True)
            worker.start()
            try:
                subprocess.run(INSTALL, check=True, env=dict(os.environ,
                    PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST=f'http://127.0.0.1:{mirror.server_port}'))
            finally:
                mirror.shutdown()
                worker.join()


if __name__ == '__main__':
    main()
