# -*- coding: utf-8 -*-
"""行儀のよいHTTPクライアント: 間隔をあけ、身元を名乗り、Cookieを保持する"""
import http.cookiejar, time, urllib.parse, urllib.request

UA = "nyusatsu-watch/0.2 (+https://github.com/smile88405-dev/nyusatsu-watch; civic research; about 1 request/sec)"
WAIT = 1.0


class Session:
    def __init__(self, encoding="utf-8", wait=WAIT):
        self.enc, self.wait = encoding, wait
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.op.addheaders = [("User-Agent", UA)]

    def get(self, url, data=None, binary=False, retries=2):
        if data is not None and not isinstance(data, bytes):
            data = urllib.parse.urlencode(data, encoding=self.enc).encode()
        for i in range(retries + 1):
            try:
                with self.op.open(urllib.request.Request(url, data=data), timeout=90) as r:
                    b = r.read()
                break
            except Exception:
                if i == retries: raise
                time.sleep(5 * (i + 1))
        time.sleep(self.wait)
        return b if binary else b.decode(self.enc, "replace")
