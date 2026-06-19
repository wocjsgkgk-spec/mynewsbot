import base64
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

REALTIME_ONLY_MINUTES = int(os.environ.get("REALTIME_ONLY_MINUTES", "30"))
RSS_SEARCH_WINDOW = os.environ.get("RSS_SEARCH_WINDOW", "1h")
MAX_RSS_ITEMS_TO_CHECK = int(os.environ.get("MAX_RSS_ITEMS_TO_CHECK", "5"))
MAX_MESSAGES_PER_RUN = int(os.environ.get("MAX_MESSAGES_PER_RUN", "4"))
try:
    TIMEZONE = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Seoul"))
except ZoneInfoNotFoundError:
    TIMEZONE = dt.timezone(dt.timedelta(hours=9), name="KST")

STATE_PATH = Path(os.environ.get("STATE_PATH", "github-actions-news-bot/sent_articles.json"))

KEYWORDS = [
    {
        "title": "🥔기업 실적 및 공시 이슈🥔",
        "keyword": "실적발표 OR 어닝서프라이즈 OR 어닝쇼크 OR 가이던스 OR 자사주매입 OR M&A OR 무상증자 OR 유상증자 OR 액면분할 OR 배당락 OR 권리락 OR 상한가 OR 하한가 OR 신고가 OR 신저가",
    },
    {
        "title": "🥔거시경제 및 시황 지표🥔",
        "keyword": "기준금리 OR CPI OR PPI OR GDP OR 환율 OR 국채금리 OR 미국10년물국채금리 OR 경기침체 OR 경기회복 OR 유동성 OR FOMC OR 점도표 OR VIX OR 공포지수 OR 달러인덱스 OR DXY OR 나스닥선물 OR S&P500선물 OR 양적완화 OR 양적긴축 OR 코스피 OR 코스닥 OR S&P500 OR NASDAQ OR DowJones",
    },
    {
        "title": "🥔미래 산업 및 핵심 테마🥔",
        "keyword": "AI OR 반도체 OR 데이터센터 OR 클라우드 OR 로봇 OR 2차전지 OR 전기차 OR 바이오 OR 원자력 OR 방산 OR 양자컴퓨터 OR 공급망 OR 리쇼어링",
    },
    {
        "title": "🥔투자 지표 및 매매 기술🥔",
        "keyword": "시가총액 OR 거래량 OR 거래대금 OR 외국인수급 OR 기관수급 OR 개인수급 OR 공매도 OR 신용잔고 OR PER OR PBR OR ROE OR EPS OR BPS OR 배당수익률 OR FCF OR 부채비율 OR 이동평균선 OR 골든크로스 OR 데드크로스 OR 지지선 OR 저항선 OR RSI OR MACD OR 볼린저밴드 OR 캔들차트 OR 거래량급증 OR 분할매수 OR 분할매도 OR 손절 OR 익절 OR 리밸런싱 OR 성장주 OR 가치주 OR 배당주 OR 테마주 OR 대장주 OR 우선주 OR 보통주",
    },
]


def main():
    if not DRY_RUN and (not BOT_TOKEN or not CHAT_ID):
        raise RuntimeError("BOT_TOKEN and CHAT_ID secrets are required.")

    state = load_state()
    sent_count = 0
    details = []

    for category in KEYWORDS:
        if sent_count >= MAX_MESSAGES_PER_RUN:
            break

        try:
            item = find_sendable_item(category, state)
        except Exception as exc:
            details.append({"category": category["title"], "error": str(exc)})
            print(f"[ERROR] {category['title']}: {exc}", file=sys.stderr)
            continue

        if not item:
            details.append({"category": category["title"], "sent": False})
            continue

        message = build_message(category["title"], item)
        if DRY_RUN:
            print("[DRY_RUN] would send:")
            print(message)
        else:
            send_to_telegram(message, item["real_link"])

        mark_sent(state, item)
        sent_count += 1
        details.append({"category": category["title"], "sent": True, "title": item["title"], "url": item["real_link"]})

    prune_state(state)
    save_state(state)
    print(json.dumps({"sent_count": sent_count, "details": details}, ensure_ascii=False, indent=2))


def find_sendable_item(category, state):
    rss_xml = http_get_text(make_rss_url(category["keyword"]))
    items = parse_rss_items(rss_xml)
    items.sort(key=lambda item: item["published_at"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)

    for item in items[:MAX_RSS_ITEMS_TO_CHECK]:
        google_link = item["google_link"]
        if not google_link:
            continue
        if not is_realtime_item(item):
            print(f"[SKIP] old article {article_age_minutes(item)}m: {item['title']}")
            continue
        if was_sent(state, google_link):
            print(f"[SKIP] already sent: {item['title']}")
            continue
        if was_failed_recently(state, google_link):
            print(f"[SKIP] recent decode failure: {item['title']}")
            continue

        real_link = get_real_url(google_link, state)
        if not real_link or "news.google.com" in real_link:
            print(f"[SKIP] failed to decode Google News URL: {google_link}")
            mark_failed(state, google_link)
            continue

        item["real_link"] = real_link
        item["summary"] = make_summary(item["description"])
        return item

    return None


def make_rss_url(keyword):
    query = f"{keyword} when:{RSS_SEARCH_WINDOW}"
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"}
    )


def parse_rss_items(xml_text):
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        return []

    items = []
    for node in channel.findall("item"):
        raw_title = node.findtext("title") or ""
        pub_date = node.findtext("pubDate") or ""
        items.append(
            {
                "title": clean_title(raw_title),
                "google_link": node.findtext("link") or "",
                "description": node.findtext("description") or "",
                "published_at": parse_pub_date(pub_date),
                "pub_date_raw": pub_date,
            }
        )
    return items


def is_realtime_item(item):
    if not item["published_at"]:
        return False
    age = dt.datetime.now(dt.timezone.utc) - item["published_at"].astimezone(dt.timezone.utc)
    return dt.timedelta(0) <= age <= dt.timedelta(minutes=REALTIME_ONLY_MINUTES)


def article_age_minutes(item):
    if not item["published_at"]:
        return 999999
    age = dt.datetime.now(dt.timezone.utc) - item["published_at"].astimezone(dt.timezone.utc)
    return int(age.total_seconds() // 60)


def get_real_url(google_url, state):
    cache_key = stable_id(google_url)
    cached = state.get("url_cache", {}).get(cache_key, {}).get("real_link", "")
    if cached:
        return cached

    old_url = decode_old_google_news_url(google_url)
    if old_url:
        cache_real_url(state, google_url, old_url)
        return old_url

    article_id = get_google_news_id(google_url)
    if not article_id:
        return google_url

    decoded = decode_google_news_by_batch(article_id)
    if decoded:
        cache_real_url(state, google_url, decoded)
        return decoded

    return google_url


def get_google_news_id(url):
    match = re.search(r"/(?:articles|read)/([^?]+)", url)
    return urllib.parse.unquote(match.group(1)) if match else ""


def decode_old_google_news_url(url):
    article_id = get_google_news_id(url)
    if not article_id:
        return ""
    try:
        padded = article_id + "=" * (-len(article_id) % 4)
        raw = base64.urlsafe_b64decode(padded)
    except Exception:
        return ""

    text = raw.decode("latin1", errors="ignore")
    direct = re.search(r"https?://[^\s\"'<>]+", text)
    if direct:
        return clean_url(direct.group(0))

    if len(text) > 3 and [ord(text[0]), ord(text[1]), ord(text[2])] == [8, 19, 34]:
        text = text[3:]
    if len(text) > 3 and [ord(text[-3]), ord(text[-2]), ord(text[-1])] == [210, 1, 0]:
        text = text[:-3]
    if len(text) > 2:
        length = ord(text[0])
        text = text[2 : length + 2] if length >= 128 else text[1 : length + 1]

    return clean_url(text) if text.startswith("http") else ""


def decode_google_news_by_batch(article_id):
    params = get_google_news_decode_params(article_id)
    if not params:
        return decode_google_news_by_simple_batch(article_id)

    inner = json.dumps(
        [
            "garturlreq",
            [
                ["ko-KR", "KR", ["FINANCE_TOP_INDICES", "WEB_TEST_1_0_0"], None, None, 1, 1, "KR:ko", None, 1, None, None, None, None, None, 0, 1],
                "ko-KR",
                "KR",
                1,
                [1, 1, 1],
                1,
                1,
                None,
                0,
                0,
                None,
                0,
            ],
            article_id,
            int(params["timestamp"]),
            params["signature"],
        ],
        separators=(",", ":"),
    )
    request_payload = json.dumps([[["Fbv4je", inner, None, "generic"]]], separators=(",", ":"))
    return extract_decoded_url(post_google_batch(request_payload))


def decode_google_news_by_simple_batch(article_id):
    inner = (
        '["garturlreq",[["ko-KR","KR",["FINANCE_TOP_INDICES","WEB_TEST_1_0_0"],null,null,1,1,'
        '"KR:ko",null,180,null,null,null,null,null,0,null,null,[1608992183,723341000]],"ko-KR",'
        f'"KR",1,[2,3,4,8],1,0,"655000234",0,0,null,0],"{article_id}"]'
    )
    request_payload = json.dumps([[["Fbv4je", inner, None, "generic"]]], separators=(",", ":"))
    return extract_decoded_url(post_google_batch(request_payload))


def get_google_news_decode_params(article_id):
    urls = [
        f"https://news.google.com/rss/articles/{article_id}?oc=5",
        f"https://news.google.com/articles/{article_id}?hl=ko&gl=KR&ceid=KR:ko",
    ]
    for url in urls:
        text = http_get_text(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )
        signature = re.search(r'data-n-a-sg=["\']([^"\']+)["\']', text)
        timestamp = re.search(r'data-n-a-ts=["\']([^"\']+)["\']', text)
        if signature and timestamp:
            return {"signature": html.unescape(signature.group(1)), "timestamp": html.unescape(timestamp.group(1))}
    return None


def post_google_batch(request_payload):
    body = urllib.parse.urlencode({"f.req": request_payload}).encode("utf-8")
    return http_request_text(
        "https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je",
        method="POST",
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://news.google.com/",
            "Origin": "https://news.google.com",
            "User-Agent": "Mozilla/5.0",
            "X-Same-Domain": "1",
        },
    )


def extract_decoded_url(text):
    try:
        json_text = text.split("\n\n", 1)[1] if "\n\n" in text else text
        outer = json.loads(json_text)
        for row in outer:
            if len(row) > 2 and row[2]:
                inner = json.loads(row[2])
                if len(inner) > 1 and inner[1]:
                    return clean_url(inner[1])
    except Exception:
        pass

    match = re.search(r'\[\\"garturlres\\",\\"(https?:.*?)(?=\\",)', text)
    if not match:
        match = re.search(r'"garturlres","(https?:.*?)(?=",)', text)
    return clean_url(match.group(1)) if match else ""


def send_to_telegram(text, preview_url):
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
        "link_preview_options": {
            "is_disabled": False,
            "url": preview_url,
            "prefer_large_media": True,
            "show_above_text": False,
        },
    }
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = http_request_text(
        api_url,
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    result = json.loads(response)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram send failed: {response}")
    return result


def build_message(category_title, item):
    date_text = format_pub_date(item["published_at"])
    return (
        f"{escape_text(category_title)}\n"
        f"<b>{escape_text(item['title'])}</b>\n\n"
        f"{escape_text(item['summary'])}\n\n"
        f"📅 {escape_text(date_text)}\n"
        f"🔗 <a href=\"{html.escape(item['real_link'])}\">원문 링크</a>"
    )


def make_summary(description):
    text = html.unescape(description or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "본문 요약 정보를 가져올 수 없습니다."
    text = text[:167] + "..." if len(text) > 170 else text
    lines = [text[i : i + 55] for i in range(0, min(len(text), 165), 55)]
    return "\n".join(line.strip() for line in lines if line.strip())


def clean_title(title):
    title = html.unescape(title or "제목 없음")
    return title.rsplit(" - ", 1)[0].strip()


def escape_text(text):
    return html.escape(str(text or ""), quote=False)


def clean_url(url):
    return html.unescape(url or "").replace("\\/", "/").replace("\\u003d", "=").replace("\\u0026", "&").rstrip(")]}").strip()


def parse_pub_date(raw):
    if not raw:
        return None
    try:
        value = parsedate_to_datetime(raw)
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def format_pub_date(value):
    if not value:
        return ""
    local_value = value.astimezone(TIMEZONE)
    ampm = "오전" if local_value.hour < 12 else "오후"
    hour = local_value.hour % 12 or 12
    return f"{local_value:%Y.%m.%d.} {ampm} {hour}:{local_value:%M}"


def load_state():
    if not STATE_PATH.exists():
        return {"sent": {}, "url_cache": {}, "failed": {}}
    with STATE_PATH.open("r", encoding="utf-8") as file:
        state = json.load(file)
    state.setdefault("sent", {})
    state.setdefault("url_cache", {})
    state.setdefault("failed", {})
    return state


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STATE_PATH.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")


def stable_id(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def was_sent(state, google_link):
    return stable_id(google_link) in state["sent"]


def mark_sent(state, item):
    state["sent"][stable_id(item["google_link"])] = {
        "title": item["title"],
        "google_link": item["google_link"],
        "real_link": item.get("real_link", ""),
        "published_at": item["published_at"].isoformat() if item.get("published_at") else "",
        "sent_at": now_iso(),
    }


def cache_real_url(state, google_link, real_link):
    state["url_cache"][stable_id(google_link)] = {
        "google_link": google_link,
        "real_link": real_link,
        "updated_at": now_iso(),
    }


def was_failed_recently(state, google_link):
    data = state["failed"].get(stable_id(google_link))
    if not data:
        return False
    try:
        failed_at = dt.datetime.fromisoformat(data["failed_at"])
    except Exception:
        return False
    return dt.datetime.now(dt.timezone.utc) - failed_at < dt.timedelta(hours=6)


def mark_failed(state, google_link):
    state["failed"][stable_id(google_link)] = {"google_link": google_link, "failed_at": now_iso()}


def prune_state(state):
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=14)
    for section, time_key in [("sent", "sent_at"), ("url_cache", "updated_at"), ("failed", "failed_at")]:
        kept = {}
        for key, value in state.get(section, {}).items():
            try:
                timestamp = dt.datetime.fromisoformat(value.get(time_key, ""))
            except Exception:
                timestamp = dt.datetime.now(dt.timezone.utc)
            if timestamp >= cutoff:
                kept[key] = value
        state[section] = kept


def http_get_text(url, headers=None):
    return http_request_text(url, headers=headers or {})


def http_request_text(url, method="GET", data=None, headers=None, timeout=25):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {body[:300]}") from exc


if __name__ == "__main__":
    main()
