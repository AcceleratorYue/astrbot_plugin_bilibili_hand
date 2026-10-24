"""B站之手：最小 HTTP 客户端（P0 只做登录检查）。

这里刻意不依赖第三方库，也不依赖 B站之眼的源码；动作接口在 P1 接入。
B 站域名会优先返回 IPv6，所以和眼睛一样强制 IPv4 直连。
"""

from __future__ import annotations

import gzip
import http.client
import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_SSL_CONTEXT = ssl.create_default_context()


class _IPv4HTTPSConnection(http.client.HTTPSConnection):
    """只走 IPv4 的 HTTPS 连接。"""

    def connect(self) -> None:
        last_error: OSError | None = None
        for family, socktype, proto, _canon, addr in socket.getaddrinfo(
            self.host, self.port, socket.AF_INET, socket.SOCK_STREAM
        ):
            sock = socket.socket(family, socktype, proto)
            sock.settimeout(self.timeout)
            try:
                sock.connect(addr)
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                return
            except OSError as exc:
                last_error = exc
                sock.close()
        raise last_error or OSError(f"{self.host} 没有可用的 IPv4 地址")


class HandError(Exception):
    """带语义原因的动作错误；code 只用于日志，不直接给用户。"""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class BiliHandClient:
    def __init__(
        self,
        sessdata: str = "",
        bili_jct: str = "",
        buvid3: str = "",
        user_agent: str = "",
        timeout: int = 20,
        interval: float = 5.0,
    ):
        self.sessdata = str(sessdata or "").strip()
        self.bili_jct = str(bili_jct or "").strip()
        self.buvid3 = str(buvid3 or "").strip()
        self.buvid4 = ""
        self.timeout = max(3, int(timeout))
        self.interval = max(0.0, float(interval))
        self._last_request = 0.0

    def cookie_header(self) -> str:
        parts = []
        if self.sessdata:
            parts.append(f"SESSDATA={self.sessdata}")
        if self.bili_jct:
            parts.append(f"bili_jct={self.bili_jct}")
        if self.buvid3:
            parts.append(f"buvid3={self.buvid3}")
        if self.buvid4:
            parts.append(f"buvid4={self.buvid4}")
        return "; ".join(parts)

    def ensure_buvid(self) -> None:
        """动作接口通常要求有效的 buvid3；缺失时向 B 站申请游客指纹。"""

        if self.buvid3:
            return
        try:
            payload = self._json("https://api.bilibili.com/x/frontend/finger/spi")
            data = payload.get("data") or {}
            b3 = str(data.get("b_3") or "").strip()
            b4 = str(data.get("b_4") or "").strip()
            if b3:
                self.buvid3 = b3
            if b4:
                self.buvid4 = b4
        except Exception:
            return

    def _throttle(self) -> None:
        if self.interval <= 0:
            return
        delta = time.monotonic() - self._last_request
        if delta < self.interval:
            time.sleep(self.interval - delta)
        self._last_request = time.monotonic()

    def _request(
        self,
        url: str,
        method: str = "GET",
        form: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        self._throttle()
        merged = {
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
        }
        cookie = self.cookie_header()
        if cookie:
            merged["Cookie"] = cookie
        if headers:
            merged.update(headers)
        data = None
        if form:
            data = urllib.parse.urlencode(form).encode("utf-8")
            merged["Content-Type"] = "application/x-www-form-urlencoded"
        parsed = urllib.parse.urlsplit(url)
        try:
            if parsed.scheme == "https":
                conn = _IPv4HTTPSConnection(
                    parsed.hostname,
                    parsed.port or 443,
                    timeout=self.timeout,
                    context=_SSL_CONTEXT,
                )
                path = parsed.path or "/"
                if parsed.query:
                    path += "?" + parsed.query
                try:
                    conn.request(method, path, body=data, headers=merged)
                    response = conn.getresponse()
                    status = response.status
                    raw = response.read()
                    encoding = response.headers.get("Content-Encoding", "")
                finally:
                    conn.close()
                if status in (412, 429):
                    raise HandError("rate_limited", f"http {status}")
                if status >= 400:
                    raise HandError("network", f"http {status}")
            else:
                request = urllib.request.Request(
                    url, data=data, headers=merged, method=method
                )
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    encoding = response.headers.get("Content-Encoding", "")
        except HandError:
            raise
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise HandError("network", str(exc)) from exc
        if raw[:2] == b"\x1f\x8b":
            return gzip.decompress(raw)
        if encoding == "deflate" or raw[:1] not in (b"{", b"[", b"<"):
            try:
                return zlib.decompress(raw)
            except Exception:
                try:
                    return zlib.decompress(raw, -zlib.MAX_WBITS)
                except Exception:
                    return raw
        return raw

    def _json(self, url: str, method: str = "GET", form: dict | None = None) -> dict:
        raw = self._request(url, method, form)
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except Exception as exc:
            raise HandError("network", "响应不是 JSON") from exc
        if not isinstance(payload, dict):
            raise HandError("network", "响应格式异常")
        return payload

    def nav(self) -> dict:
        """检查登录态，返回 {logged_in, mid, uname}；不记录 Cookie。"""

        if not self.sessdata:
            raise HandError("need_login", "未配置 SESSDATA")
        payload = self._json("https://api.bilibili.com/x/web-interface/nav")
        code = int(payload.get("code") or 0)
        if code == -101:
            raise HandError("need_login", "SESSDATA 已失效")
        if code != 0:
            raise HandError("network", f"nav code={code}")
        data = payload.get("data") or {}
        if not data.get("isLogin"):
            raise HandError("need_login", "当前 Cookie 不是登录态")
        return {
            "logged_in": True,
            "mid": int(data.get("mid") or 0),
            "uname": str(data.get("uname") or ""),
        }

    # -- 动作 -----------------------------------------------------------

    def _require_csrf(self) -> None:
        if not self.bili_jct:
            raise HandError("csrf", "未配置 bili_jct")

    def _action(
        self, url: str, form: dict, extra_errors: dict[int, str] | None = None
    ) -> dict:
        self._require_csrf()
        self.ensure_buvid()
        payload = self._json(url, "POST", form)
        code = int(payload.get("code") or 0)
        if code == 0:
            data = payload.get("data")
            return data if isinstance(data, dict) else {}
        errors = {
            -101: "need_login",
            -102: "account_banned",
            -111: "csrf",
            -352: "rate_limited",
            -412: "rate_limited",
            -509: "rate_limited",
            -104: "insufficient_coins",
            10003: "not_found",
            65004: "failed",
            65006: "already_done",
            34002: "self_not_allowed",
            34004: "rate_limited",
            34005: "daily_limit",
            12002: "comment_closed",
            12015: "captcha",
            12016: "sensitive",
            12025: "too_long",
            12051: "duplicate",
            12035: "blacklisted",
        }
        if extra_errors:
            errors.update(extra_errors)
        raise HandError(errors.get(code, "failed"), f"code={code}")

    def has_like(self, aid: int) -> bool:
        self.ensure_buvid()
        payload = self._json(
            f"https://api.bilibili.com/x/web-interface/archive/has/like?aid={int(aid)}"
        )
        if int(payload.get("code") or 0) != 0:
            return False
        data = payload.get("data")
        return bool(data in (1, True) or (isinstance(data, dict) and data.get("like")))

    def coin_count(self, aid: int) -> int:
        self.ensure_buvid()
        payload = self._json(
            f"https://api.bilibili.com/x/web-interface/archive/coins?aid={int(aid)}"
        )
        if int(payload.get("code") or 0) != 0:
            return 0
        data = payload.get("data") or {}
        try:
            return int(data.get("multiply") or 0)
        except Exception:
            return 0

    def like(self, aid: int) -> dict:
        return self._action(
            "https://api.bilibili.com/x/web-interface/archive/like",
            {"aid": int(aid), "like": 1, "csrf": self.bili_jct},
        )

    def coin(self, aid: int, multiply: int = 1) -> dict:
        return self._action(
            "https://api.bilibili.com/x/web-interface/coin/add",
            {
                "aid": int(aid),
                "multiply": max(1, min(2, int(multiply))),
                "select_like": 0,
                "csrf": self.bili_jct,
            },
        )

    def fav_folders(self, mid: int) -> list[dict]:
        self.ensure_buvid()
        payload = self._json(
            "https://api.bilibili.com/x/v3/fav/folder/created/list-all"
            f"?up_mid={int(mid)}"
        )
        if int(payload.get("code") or 0) != 0:
            return []
        data = payload.get("data") or {}
        folders = data.get("list") or []
        return [item for item in folders if isinstance(item, dict) and item.get("id")]

    def is_favoured(self, aid: int) -> bool:
        self.ensure_buvid()
        payload = self._json(
            f"https://api.bilibili.com/x/v2/fav/video/favoured?aid={int(aid)}"
        )
        if int(payload.get("code") or 0) != 0:
            return False
        data = payload.get("data") or {}
        return bool(data.get("favoured")) if isinstance(data, dict) else False

    def favorite(self, aid: int, folder_id: int) -> dict:
        return self._action(
            "https://api.bilibili.com/x/v3/fav/resource/deal",
            {
                "rid": int(aid),
                "type": 2,
                "add_media_ids": int(folder_id),
                "del_media_ids": "",
                "csrf": self.bili_jct,
            },
        )

    def comment(self, aid: int, message: str, root: int = 0, parent: int = 0) -> dict:
        form = {
            "type": 1,
            "oid": int(aid),
            "message": str(message),
            "plat": 1,
            "csrf": self.bili_jct,
        }
        if root:
            form["root"] = int(root)
        if parent:
            form["parent"] = int(parent)
        return self._action(
            "https://api.bilibili.com/x/v2/reply/add",
            form,
        )


