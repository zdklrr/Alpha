from __future__ import annotations

import asyncio
import datetime as _dt
import re
import time
from typing import Any

import httpx
import pandas as pd


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, str):
            text = value.replace(",", "").strip()
            if text in {"", "-", "--", "None", "nan", "NaN"}:
                return default
            return float(text)
        return float(value)
    except Exception:
        return default


def _normalize_symbol(symbol: str) -> str:
    digits = "".join(ch for ch in str(symbol or "") if ch.isdigit())
    return digits[-6:].zfill(6) if digits else ""


def _yyyymmdd(value: str) -> str:
    text = str(value or "").strip().replace("-", "")
    if len(text) >= 8 and text[:8].isdigit():
        return text[:8]
    return ""


def _iso_date(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.date().isoformat()


class EastmoneyMarketDataClient:
    """Async, timeout-bound market data client for K-line cache sync.

    This client intentionally avoids AkShare's blocking requests path for the
    data-center sync workflow. Every network call goes through httpx with
    connect/read/write/pool timeouts so a bad upstream cannot strand a Python
    thread in OpenSSL/socket forever.
    """

    HIST_URLS = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        "https://82.push2his.eastmoney.com/api/qt/stock/kline/get",
    )
    SPOT_URLS = (
        "https://82.push2.eastmoney.com/api/qt/clist/get",
        "https://push2.eastmoney.com/api/qt/clist/get",
    )
    TRADE_DAYS_URL = "https://finance.sina.com.cn/realstock/company/klc_td_sh.txt"
    TENCENT_KLINE_URLS = (
        "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
        "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
    )
    # 东财 api/qt 路径在部分网络环境会整段 403；连续失败后先走腾讯备用源，
    # 避免每个标的都把超时/重试时间耗满（整轮同步会慢一个数量级）。
    EM_FAILURE_THRESHOLD = 3
    EM_COOLDOWN_SECONDS = 600.0
    # 腾讯接口对高频访问会返回 501 反爬页面：全局限速 + 单域名连续失败后冷却。
    TENCENT_MIN_INTERVAL = 0.2
    TENCENT_HOST_COOLDOWN = 300.0
    TENCENT_HOST_FAIL_LIMIT = 2

    def __init__(
        self,
        *,
        store: Any | None = None,
        timeout: httpx.Timeout | None = None,
        retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.store = store
        self.timeout = timeout or httpx.Timeout(connect=2.0, read=8.0, write=2.0, pool=2.0)
        self.retries = max(0, int(retries))
        self.transport = transport
        self._em_fail_streak = 0
        self._em_blocked_until = 0.0
        self._tencent_host_fails: dict[str, int] = {}
        self._tencent_host_blocked: dict[str, float] = {}
        self._tencent_lock: asyncio.Lock | None = None
        self._tencent_last_request = 0.0
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
        }

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout,
            headers=self._headers,
            trust_env=False,
            follow_redirects=True,
            transport=self.transport,
        )

    async def _get_json(self, client: httpx.AsyncClient, url: str, params: dict[str, Any]) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                payload = resp.json()
                return payload if isinstance(payload, dict) else {}
            except Exception as exc:
                last_exc = exc
                if attempt < self.retries:
                    await asyncio.sleep(0.25 * (attempt + 1))
        print(f"[market_data_client] GET json failed: {url} ({last_exc})")
        return {}

    async def _get_text(self, client: httpx.AsyncClient, url: str) -> str:
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text
            except Exception as exc:
                last_exc = exc
                if attempt < self.retries:
                    await asyncio.sleep(0.25 * (attempt + 1))
        print(f"[market_data_client] GET text failed: {url} ({last_exc})")
        return ""

    async def fetch_hist(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        clean_symbol = _normalize_symbol(symbol)
        if not clean_symbol:
            return pd.DataFrame()
        market_code = "1" if clean_symbol.startswith(("6", "68")) else "0"
        adjust_dict = {"qfq": "1", "hfq": "2", "": "0", None: "0"}
        params = {
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116",
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "klt": "101",
            "fqt": adjust_dict.get(adjust, "1"),
            "secid": f"{market_code}.{clean_symbol}",
            "beg": _yyyymmdd(start_date),
            "end": _yyyymmdd(end_date),
        }
        if not params["beg"] or not params["end"]:
            return pd.DataFrame()

        payload: dict[str, Any] = {}
        em_available = self._eastmoney_available()
        if em_available:
            async with self._client() as client:
                for url in self.HIST_URLS:
                    payload = await self._get_json(client, url, params)
                    data = payload.get("data") if isinstance(payload, dict) else None
                    klines = data.get("klines") if isinstance(data, dict) else None
                    if klines:
                        self._eastmoney_ok()
                        break
                else:
                    self._eastmoney_failed()

        data = payload.get("data") if isinstance(payload, dict) else None
        klines = data.get("klines") if isinstance(data, dict) else None
        if not klines:
            # 东财不可用（403/超时）→ 走腾讯备用源，保证整轮同步能拿到 K 线
            return await self._fetch_hist_tencent(clean_symbol, start_date, end_date, adjust)

        rows: list[list[str]] = []
        for item in klines:
            parts = str(item).split(",")
            if len(parts) >= 7:
                rows.append(parts)
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        out = pd.DataFrame()
        out["日期"] = pd.to_datetime(df[0], errors="coerce").dt.date.astype(str)
        out["开盘"] = pd.to_numeric(df[1], errors="coerce").fillna(0.0)
        out["收盘"] = pd.to_numeric(df[2], errors="coerce").fillna(0.0)
        out["最高"] = pd.to_numeric(df[3], errors="coerce").fillna(0.0)
        out["最低"] = pd.to_numeric(df[4], errors="coerce").fillna(0.0)
        out["成交量"] = pd.to_numeric(df[5], errors="coerce").fillna(0.0)
        out["成交额"] = pd.to_numeric(df[6], errors="coerce").fillna(0.0)
        out = out[out["日期"].ne("NaT")]
        out = out.reset_index(drop=True)
        out.attrs["data_source"] = "eastmoney"
        out.attrs["fallback_reason"] = ""
        return out

    # ------------------------------------------------------------------
    # 东财可用性熔断 + 腾讯备用数据源
    # ------------------------------------------------------------------
    def _eastmoney_available(self) -> bool:
        if self._em_fail_streak < self.EM_FAILURE_THRESHOLD:
            return True
        return time.monotonic() >= self._em_blocked_until

    def _eastmoney_failed(self) -> None:
        self._em_fail_streak += 1
        if self._em_fail_streak >= self.EM_FAILURE_THRESHOLD:
            self._em_blocked_until = time.monotonic() + self.EM_COOLDOWN_SECONDS
            print(
                "[market_data_client] eastmoney kline unavailable, "
                f"fallback to tencent for {self.EM_COOLDOWN_SECONDS:.0f}s"
            )

    def _eastmoney_ok(self) -> None:
        self._em_fail_streak = 0
        self._em_blocked_until = 0.0

    def _tencent_hosts(self) -> list[str]:
        now = time.monotonic()
        hosts = [
            url
            for url in self.TENCENT_KLINE_URLS
            if self._tencent_host_blocked.get(url, 0.0) <= now
        ]
        return hosts or list(self.TENCENT_KLINE_URLS[:1])

    def _tencent_host_failed(self, url: str) -> None:
        fails = self._tencent_host_fails.get(url, 0) + 1
        self._tencent_host_fails[url] = fails
        if fails >= self.TENCENT_HOST_FAIL_LIMIT:
            self._tencent_host_blocked[url] = time.monotonic() + self.TENCENT_HOST_COOLDOWN
            self._tencent_host_fails[url] = 0
            print(f"[market_data_client] tencent host cooled down: {url}")

    def _tencent_host_ok(self, url: str) -> None:
        self._tencent_host_fails[url] = 0

    async def _tencent_throttle(self) -> None:
        if self._tencent_lock is None:
            self._tencent_lock = asyncio.Lock()
        async with self._tencent_lock:
            elapsed = time.monotonic() - self._tencent_last_request
            wait = self.TENCENT_MIN_INTERVAL - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
            self._tencent_last_request = time.monotonic()

    @staticmethod
    def _tencent_symbol(clean_symbol: str) -> str:
        if clean_symbol.startswith(("6", "9", "5")):
            return f"sh{clean_symbol}"
        if clean_symbol.startswith(("4", "8")):
            return f"bj{clean_symbol}"
        return f"sz{clean_symbol}"

    @staticmethod
    def _tencent_date(value: str) -> str:
        text = _yyyymmdd(value)
        if not text:
            return ""
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"

    @staticmethod
    def _tencent_count(start_date: str, end_date: str) -> int:
        try:
            days = (
                _dt.date.fromisoformat(end_date) - _dt.date.fromisoformat(start_date)
            ).days
        except Exception:
            days = 120
        return int(max(30, min(640, days + 20)))

    async def _fetch_hist_tencent(
        self,
        clean_symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """腾讯备用源日 K（多域名轮换 + 限速 + 失败冷却）。

        返回与东财一致的中文列结构；腾讯不返回成交额，这里用
        收盘价 × 成交量(手) × 100 估算，供下游成交额类因子使用。
        """
        sym = self._tencent_symbol(clean_symbol)
        fq = {"qfq": "qfq", "hfq": "hfq"}.get(str(adjust or "").lower(), "")
        begin = self._tencent_date(start_date)
        finish = self._tencent_date(end_date)
        if not begin or not finish:
            return pd.DataFrame()
        count = self._tencent_count(begin, finish)
        params = {"param": f"{sym},day,{begin},{finish},{count},{fq}"}

        payload: dict[str, Any] = {}
        for url in self._tencent_hosts():
            await self._tencent_throttle()
            try:
                async with self._client() as client:
                    payload = await self._get_json(client, url, params)
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[market_data_client] tencent GET failed: {url} ({exc})")
                payload = {}
            if payload:
                self._tencent_host_ok(url)
                break
            self._tencent_host_failed(url)
        if not payload:
            return pd.DataFrame()

        data = payload.get("data") if isinstance(payload, dict) else None
        node = data.get(sym) if isinstance(data, dict) else None
        if not isinstance(node, dict):
            return pd.DataFrame()
        key = f"{fq}day" if fq else "day"
        items = node.get(key) or node.get("day") or []
        rows: list[list[str]] = []
        for item in items:
            if isinstance(item, (list, tuple)) and len(item) >= 6:
                rows.append(list(item))
        if not rows:
            return pd.DataFrame()

        out = pd.DataFrame()
        out["日期"] = (
            pd.to_datetime(pd.Series([r[0] for r in rows]), errors="coerce")
            .dt.date.astype(str)
        )
        out["开盘"] = pd.to_numeric(pd.Series([r[1] for r in rows]), errors="coerce").fillna(0.0)
        out["收盘"] = pd.to_numeric(pd.Series([r[2] for r in rows]), errors="coerce").fillna(0.0)
        out["最高"] = pd.to_numeric(pd.Series([r[3] for r in rows]), errors="coerce").fillna(0.0)
        out["最低"] = pd.to_numeric(pd.Series([r[4] for r in rows]), errors="coerce").fillna(0.0)
        out["成交量"] = pd.to_numeric(pd.Series([r[5] for r in rows]), errors="coerce").fillna(0.0)
        # 腾讯日 K 的成交量单位：主板/创业板为「手」，科创板(688/689)为「股」。
        # 统一折算成「手」，与东财口径保持一致。
        if clean_symbol.startswith(("688", "689")):
            out["成交量"] = out["成交量"] / 100.0
        out["成交额"] = (out["收盘"] * out["成交量"] * 100.0).round(2)
        out = out[out["日期"].ne("NaT")]
        out = out.reset_index(drop=True)
        out.attrs["data_source"] = "tencent"
        out.attrs["fallback_reason"] = "eastmoney_unavailable"
        return out

    async def fetch_spot(self) -> pd.DataFrame:
        for url in self.SPOT_URLS:
            result = await self._fetch_spot_from_url(url)
            if not result.empty:
                result.attrs["data_source"] = "eastmoney"
                result.attrs["fallback_reason"] = ""
                return result
        return pd.DataFrame()

    async def _fetch_spot_from_url(self, url: str) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        page_size = 500
        async with self._client() as client:
            for page in range(1, 30):
                params = {
                    "pn": str(page),
                    "pz": str(page_size),
                    "po": "1",
                    "np": "1",
                    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                    "fltt": "2",
                    "invt": "2",
                    "fid": "f12",
                    "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
                    "fields": (
                        "f2,f3,f4,f5,f6,f12,f14,f15,f16,f17,f18,f20"
                    ),
                }
                payload = await self._get_json(client, url, params)
                data = payload.get("data") if isinstance(payload, dict) else None
                diff = data.get("diff") if isinstance(data, dict) else None
                if isinstance(diff, dict):
                    diff_rows = list(diff.values())
                elif isinstance(diff, list):
                    diff_rows = diff
                else:
                    diff_rows = []
                if not diff_rows:
                    break
                rows.extend([r for r in diff_rows if isinstance(r, dict)])
                total = int(_to_float(data.get("total"), 0)) if isinstance(data, dict) else 0
                if total and len(rows) >= total:
                    break
        return self._normalize_spot_rows(rows)

    @staticmethod
    def _normalize_spot_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame()
        items: list[dict[str, Any]] = []
        for row in rows:
            symbol = _normalize_symbol(str(row.get("f12") or ""))
            name = str(row.get("f14") or "").strip()
            if not symbol or not name:
                continue
            latest = _to_float(row.get("f2"))
            items.append(
                {
                    "代码": symbol,
                    "名称": name,
                    "最新价": latest,
                    "涨跌幅": _to_float(row.get("f3")),
                    "涨跌额": _to_float(row.get("f4")),
                    "成交量": _to_float(row.get("f5")),
                    "成交额": _to_float(row.get("f6")),
                    "最高": _to_float(row.get("f15"), latest),
                    "最低": _to_float(row.get("f16"), latest),
                    "今开": _to_float(row.get("f17"), latest),
                    "昨收": _to_float(row.get("f18"), latest),
                    "总市值": _to_float(row.get("f20"), 0.0),
                }
            )
        return pd.DataFrame(items)

    async def fetch_trade_days(self, min_days: int = 0) -> pd.DataFrame:
        db_dates: list[str] = []
        if self.store is not None:
            try:
                db_dates = self.store.get_trade_dates_from_db() or []
            except Exception:
                db_dates = []
        if self._calendar_cache_is_fresh(db_dates) and (min_days <= 0 or len(db_dates) >= min_days):
            frame = pd.DataFrame({"trade_date": db_dates})
            frame.attrs["data_source"] = "sqlite"
            frame.attrs["fallback_reason"] = ""
            return frame

        async with self._client() as client:
            text = await self._get_text(client, self.TRADE_DAYS_URL)
        dates = self._parse_sina_trade_days(text)
        if dates:
            frame = pd.DataFrame({"trade_date": dates})
            frame.attrs["data_source"] = "sina"
            frame.attrs["fallback_reason"] = ""
            return frame
        if self._calendar_cache_is_fresh(db_dates):
            frame = pd.DataFrame({"trade_date": db_dates})
            frame.attrs["data_source"] = "sqlite"
            frame.attrs["fallback_reason"] = "sina_empty_response"
            return frame
        return pd.DataFrame(columns=["trade_date"])

    @staticmethod
    def _calendar_cache_is_fresh(dates: list[str]) -> bool:
        parsed = pd.to_datetime(pd.Series(dates, dtype="object"), errors="coerce").dropna()
        if parsed.empty:
            return False
        latest = parsed.max().date()
        today = _dt.date.today()
        return today - _dt.timedelta(days=14) <= latest <= today + _dt.timedelta(days=7)

    @staticmethod
    def _parse_sina_trade_days(text: str) -> list[str]:
        if not text:
            return []
        matches = re.findall(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{8}", text)
        plain_dates = sorted({d for d in (_iso_date(item) for item in matches) if d})
        if plain_dates:
            return plain_dates

        dates: list[str] = []
        try:
            from akshare.stock.cons import hk_js_decode

            # mini-racer >= 0.12 exports MiniRacer at package top level, while the
            # legacy py-mini-racer wheel exposes the py_mini_racer submodule.
            try:
                from py_mini_racer import MiniRacer
            except ImportError:  # pragma: no cover - legacy layout
                from py_mini_racer import py_mini_racer as _py_mini_racer

                MiniRacer = _py_mini_racer.MiniRacer

            encoded = text.split("=", 1)[1].split(";", 1)[0].replace('"', "")
            js_code = MiniRacer()
            js_code.eval(hk_js_decode)
            decoded = js_code.call("d", encoded)
            dates = [_iso_date(str(item)) for item in decoded]
        except Exception:
            return []

        return sorted({d for d in dates if d})
