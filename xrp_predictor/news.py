"""News search and simple sentiment for stocks and crypto.

Headlines come from Google News RSS (no API key): English edition for US
stocks and crypto, Turkish edition for Borsa Istanbul stocks.  Each
headline is scored with a small finance word list (English + Turkish):
positive words push +1, negative words push -1.

This is a KEYWORD scan, not deep language understanding — treat the
result as a quick mood check, never as a reason to trade by itself.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from html import unescape

import requests

from .assets import Asset

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) rss-client/1.0"}

POSITIVE_WORDS = {
    # English
    "surge", "surges", "soar", "soars", "rally", "rallies", "record",
    "beat", "beats", "upgrade", "upgraded", "profit", "profits", "growth",
    "gain", "gains", "jump", "jumps", "strong", "bullish", "buy",
    "outperform", "rise", "rises", "rebound", "breakout", "high", "boost",
    "dividend", "expands", "wins", "win", "approval", "partnership",
    # Turkish
    "rekor", "kazanç", "yükseliş", "yükseldi", "büyüme", "arttı", "artış",
    "güçlü", "kâr", "kar", "temettü", "anlaşma", "ihale", "onay", "zirve",
    "toparlanma", "alım",
}

NEGATIVE_WORDS = {
    # English
    "fall", "falls", "drop", "drops", "plunge", "plunges", "crash",
    "crashes", "miss", "misses", "downgrade", "downgraded", "loss",
    "losses", "lawsuit", "probe", "investigation", "fraud", "weak",
    "bearish", "sell", "selloff", "sell-off", "underperform", "decline",
    "declines", "slump", "slumps", "cut", "cuts", "warning", "bankruptcy",
    "recall", "fine", "fined", "layoffs", "tumble", "tumbles", "low",
    # Turkish
    "düşüş", "düştü", "zarar", "dava", "soruşturma", "ceza", "geriledi",
    "azaldı", "kayıp", "iflas", "uyarı", "satış baskısı", "çöküş", "dip",
}

_WORD_RE = re.compile(r"[\w'-]+", re.UNICODE)


def headline_sentiment(title: str) -> int:
    """-1 (negative), 0 (neutral) or +1 (positive)."""
    words = {w.lower() for w in _WORD_RE.findall(title)}
    pos = len(words & POSITIVE_WORDS)
    neg = len(words & NEGATIVE_WORDS)
    lowered = title.lower()
    neg += sum(1 for phrase in ("sell-off", "satış baskısı") if phrase in lowered)
    if pos > neg:
        return 1
    if neg > pos:
        return -1
    return 0


def parse_rss(xml_text: str, limit: int = 25) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"bad RSS feed: {exc}") from exc
    items = []
    for item in root.iter("item"):
        title = unescape((item.findtext("title") or "").strip())
        if not title:
            continue
        source = (item.findtext("source") or "").strip()
        published = (item.findtext("pubDate") or "").strip()
        items.append({"title": title, "source": source,
                      "published": published})
        if len(items) >= limit:
            break
    return items


def news_query(asset: Asset) -> tuple[str, dict]:
    """Search query and Google News locale parameters for an asset."""
    if asset.kind == "tr_stock":
        return (f"{asset.symbol} hisse",
                {"hl": "tr", "gl": "TR", "ceid": "TR:tr"})
    if asset.kind == "crypto":
        return (f"{asset.symbol} crypto",
                {"hl": "en-US", "gl": "US", "ceid": "US:en"})
    return (f"{asset.symbol} stock",
            {"hl": "en-US", "gl": "US", "ceid": "US:en"})


def fetch_news(asset: Asset, limit: int = 20) -> list[dict]:
    """Fetch recent headlines for an asset, each tagged with sentiment."""
    query, locale = news_query(asset)
    resp = requests.get("https://news.google.com/rss/search",
                        params={"q": query, **locale},
                        headers=HEADERS, timeout=20)
    resp.raise_for_status()
    items = parse_rss(resp.text, limit=limit)
    for item in items:
        item["sentiment"] = headline_sentiment(item["title"])
    return items


def summarize(items: list[dict]) -> dict:
    pos = sum(1 for i in items if i.get("sentiment", 0) > 0)
    neg = sum(1 for i in items if i.get("sentiment", 0) < 0)
    n = len(items)
    net = (pos - neg) / n if n else 0.0
    if n == 0:
        label = "no recent headlines found"
    elif net >= 0.2:
        label = "leaning positive"
    elif net <= -0.2:
        label = "leaning negative"
    else:
        label = "mixed/neutral"
    return {"n": n, "positive": pos, "negative": neg,
            "neutral": n - pos - neg, "net": round(net, 3), "label": label}


def sentiment_tag(sentiment: int) -> str:
    return {1: "[+]", -1: "[-]"}.get(sentiment, "[ ]")
