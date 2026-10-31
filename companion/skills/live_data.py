"""The only online calls the companion makes: weather, FX rates, Wikipedia
and news headlines. Each tool is a plain dict so new ones can be registered
without touching the router (open/closed: router iterates the registry).
"""
import re
import threading
import time

import requests

from .. import config
from ..web.face_server import set_status
from ..audio.playback import speak


def get_weather(question=""):
    try:
        url = (f"https://api.open-meteo.com/v1/forecast?latitude={config.CITY_LAT}"
               f"&longitude={config.CITY_LON}&current=temperature_2m")
        data = requests.get(url, timeout=config.LIVE_TIMEOUT).json()["current"]
        return f"In {config.CITY_NAME} it is currently {data['temperature_2m']} degrees Celsius."
    except Exception:
        return "I could not reach the weather service right now -- check this device's internet connection."


def get_weather_tomorrow(question=""):
    try:
        url = (f"https://api.open-meteo.com/v1/forecast?latitude={config.CITY_LAT}"
               f"&longitude={config.CITY_LON}&daily=temperature_2m_max,temperature_2m_min"
               f"&timezone=auto&forecast_days=2")
        d = requests.get(url, timeout=config.LIVE_TIMEOUT).json()["daily"]
        hi, lo = d["temperature_2m_max"][1], d["temperature_2m_min"][1]
        return (f"Tomorrow in {config.CITY_NAME}, expect a high of {hi} and a low of "
                f"{lo} degrees Celsius.")
    except Exception:
        return "I could not reach the weather service right now -- check this device's internet connection."


def get_fx(question=""):
    m = re.search(r"\b([a-z]{3})\b[^a-z]+to[^a-z]+\b([a-z]{3})\b", question.lower())
    base, target = (m.group(1).upper(), m.group(2).upper()) if m else ("USD", "INR")
    try:
        url = f"https://api.frankfurter.app/latest?from={base}&to={target}"
        rate = requests.get(url, timeout=config.LIVE_TIMEOUT).json()["rates"][target]
        return f"One {base} is {rate} {target}."
    except Exception:
        return "I could not reach the exchange rate service right now -- check this device's internet connection."


def get_wikipedia(question=""):
    topic = re.sub(r"^(who|what|where|when)\s+(is|was|are|were)\s+", "",
                   question.strip(), flags=re.I)
    topic = re.sub(r"^(tell me about|look up|search (for|wikipedia for)?)\s+", "",
                   topic, flags=re.I).strip(" ?.")
    if not topic:
        return "I am not sure what to look up."
    try:
        import urllib.parse
        url = ("https://en.wikipedia.org/api/rest_v1/page/summary/"
               + urllib.parse.quote(topic.replace(" ", "_")))
        extract = requests.get(url, timeout=config.LIVE_TIMEOUT).json().get("extract")
        return extract or "I could not find anything on that."
    except Exception:
        return "I could not reach Wikipedia right now -- check this device's internet connection, or ask about a well-known topic offline."


def get_news(question=""):
    try:
        ids = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json",
                           timeout=config.LIVE_TIMEOUT).json()[:3]
        titles = []
        for i in ids:
            item = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{i}.json",
                                timeout=config.LIVE_TIMEOUT).json()
            if item and item.get("title"):
                titles.append(item["title"])
        return "Top headlines: " + "; ".join(titles) if titles else "I could not get the headlines."
    except Exception:
        return "I could not reach the news service right now."


# Each tool announces itself before its single network call. "late" tools are
# only matched after the policy documents, so broad phrasing never hijacks them.
# "followup" is an optional proactive offer shown after the answer:
#   interactive=True  -> stored in state.pending_followup, run on a "yes"
#   interactive=False -> just a spoken hint, nothing to run later
LIVE_TOOLS = [
    {"name": "weather", "source": "the Open-Meteo weather service", "note": "location + temperature",
     "host": "api.open-meteo.com", "ground": True, "late": False, "fetch": get_weather,
     "patterns": re.compile(
         r"\b(weather|temperature|how (hot|cold|warm)|raining|degrees|forecast)\b", re.I),
     "followup": {"kind": "weather_tomorrow", "interactive": True,
                  "prompt": "Would you like tomorrow's forecast too?"}},
    {"name": "fx", "source": "the exchange rate service", "note": "currency pair only",
     "host": "api.frankfurter.app", "ground": True, "late": False, "fetch": get_fx,
     "patterns": re.compile(
         r"\b(exchange rate|conversion rate|currency|[a-z]{3} to [a-z]{3})\b", re.I),
     "followup": {"kind": "fx_watch_hint", "interactive": False,
                  "prompt": "By the way, you can say, notify me if U S D to I N R crosses a "
                            "number, and I'll let you know when it does."}},
    {"name": "wikipedia", "source": "Wikipedia", "note": "search topic only",
     "host": "en.wikipedia.org", "ground": True, "late": True, "fetch": get_wikipedia,
     "patterns": re.compile(
         r"\b(who (is|was)|tell me about|look up|search wikipedia)\b", re.I)},
    {"name": "news", "source": "the news service", "note": "headline request only",
     "host": "hacker-news.firebaseio.com", "ground": True, "late": True, "fetch": get_news,
     "patterns": re.compile(r"\b(news|headlines|current events|what's happening)\b", re.I)},
]


def egress_log(host, question):
    try:
        with open(config.EGRESS_LOG, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{host}\t{question[:60]}\n")
    except Exception:
        pass


def match_live_tool(question, late):
    if not config.LIVE_DATA_ENABLED:
        return None
    for tool in LIVE_TOOLS:
        if tool["late"] == late and tool["patterns"].search(question):
            return tool
    return None


def fetch_live(tool, question):
    """Announce (console + voice), fetch once, log the call, return the data.

    The network call starts in the background the moment the spoken
    announcement begins, so the two overlap instead of stacking (speaking
    "Getting data from..." no longer adds its own duration on top of the
    fetch's latency).
    """
    host = tool["host"]
    print(f"[fetch] fetching live data from {tool['source']} ({host})")
    if host not in config.EGRESS_ALLOWLIST:
        return f"I am not allowed to contact {host}."
    set_status("fetching", heard=question, reply=f"Fetching from {host}...",
               egress=f"\u2191 {host} \u2014 {tool.get('note', 'request only')}")
    result = {}
    fetch_thread = threading.Thread(target=lambda: result.update(data=tool["fetch"](question)), daemon=True)
    fetch_thread.start()
    speak(f"Getting data from {tool['source']}.")
    fetch_thread.join()
    egress_log(host, question)
    return result.get("data", f"I could not reach {tool['source']} right now.")


def announced_fetch(host, source, fn, question, note="request only"):
    """Same announce/log pattern as fetch_live, for tools called without a
    LIVE_TOOLS entry (e.g. the proactive follow-up). Also overlaps the
    announcement with the fetch rather than speaking then fetching."""
    print(f"[fetch] fetching live data from {source} ({host})")
    if host not in config.EGRESS_ALLOWLIST:
        return f"I am not allowed to contact {host}."
    set_status("fetching", heard=question, reply=f"Fetching from {host}...",
               egress=f"\u2191 {host} \u2014 {note}")
    result = {}
    fetch_thread = threading.Thread(target=lambda: result.update(data=fn()), daemon=True)
    fetch_thread.start()
    speak(f"Getting data from {source}.")
    fetch_thread.join()
    egress_log(host, question)
    return result.get("data", f"I could not reach {source} right now.")


def privacy_report():
    try:
        lines = [l for l in open(config.EGRESS_LOG).read().splitlines() if l.strip()]
    except Exception:
        lines = []
    if not lines:
        return "I have not made any external calls. Everything has stayed on this device."
    from collections import Counter
    hosts = Counter(l.split("\t")[1] for l in lines if len(l.split("\t")) > 1)
    parts = ", ".join(f"{n} to {h}" for h, n in hosts.items())
    return f"I have made {len(lines)} external calls: {parts}. Everything else stayed on the device."
