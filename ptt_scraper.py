"""
咖啡品牌分析 - Step 6: PTT 文章與留言爬蟲

做的事：
1. 在指定看板用 PTT 內建的標題搜尋，找出標題提到各品牌的文章
2. 進入每篇文章，抓內文和推文（推／噓／→）
3. 推文的「推」「噓」本身就是現成的正負面標記，可以當情緒分析的參考答案

設計：
- 每次請求之間停 1 秒，對 PTT 伺服器客氣
- 抓過的搜尋結果與文章都存進快取，中斷後重跑會從斷點繼續，不會重抓

安裝套件：
    pip install requests beautifulsoup4 pandas

執行：
    python ptt_scraper.py
    （補抓那一批大約要 30 到 60 分鐘，中途可以 Ctrl+C，之後重跑會接著抓）

輸出：
    ptt_articles.csv   每篇文章一列（品牌、看板、標題、日期、內文、推噓數）
    ptt_comments.csv   每則推文一列（品牌、文章網址、推／噓／→、留言內容）
"""

import json
import os
import re
import time
from datetime import datetime

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE = "https://www.ptt.cc"

BRAND_QUERIES = {
    "星巴克": ["星巴克", "Starbucks"],
    "路易莎": ["路易莎", "Louisa"],
    "Cama": ["cama"],
    "怡客咖啡": ["怡客"],
    "丹堤咖啡": ["丹堤"],
}

# 兩批搜尋：
# 第一批是原本的全面搜尋（已經抓過，會直接讀快取）
# 第二批是「體驗口碑補抓」：只搜討論體驗的看板、只針對主要比較的三個品牌、多翻幾頁，
#        並且在搜尋階段就跳過優惠情報、器材交易這類文章，省下抓取時間
RUNS = [
    {
        "名稱": "全面搜尋",
        "boards": ["Coffee", "Food", "CVS", "Lifeismoney", "Gossiping"],
        "brands": list(BRAND_QUERIES),
        "max_pages": 3,
        "skip_title": None,
    },
    {
        "名稱": "體驗口碑補抓",
        "boards": ["Coffee", "Food", "Gossiping"],
        "brands": ["星巴克", "路易莎", "Cama"],
        "max_pages": 15,
        "skip_title": r"\[(情報|省錢|優惠|廣宣|徵求|交易|器材|二手|交換|轉讓|販售)\]",
    },
]

# 標題含這些字的是搜錯的文章（例如咖啡機 Moccamaster、豆種 Pacamara 都含 cama）
FALSE_POSITIVE = r"moccamaster|pacamara"

# 文章類型：依標題的 [標籤] 分類，之後情緒分析會分開看
TYPE_MAP = {
    "優惠情報": ["情報", "省錢", "優惠", "廣宣", "商品"],
    "新聞": ["新聞", "爆卦"],
    "心得食記": ["食記", "心得", "單品", "手沖", "豆豆"],
    "討論": ["問卦", "問題", "其它", "其他", "閒聊", "請益", "討論", "黑特"],
}

DELAY = 1.0        # 每次請求間隔秒數
SEARCH_CACHE = "ptt_search_cache.json"
ARTICLE_CACHE = "ptt_article_cache.jsonl"

session = requests.Session()
session.cookies.set("over18", "1")  # 八卦板等看板需要「已滿 18 歲」的 cookie
session.headers["User-Agent"] = "coffee-bi-portfolio/1.0 (student research)"


def get(url, params=None):
    for attempt in range(3):
        try:
            resp = session.get(url, params=params, timeout=20)
            if resp.status_code == 404:
                return None
            if resp.status_code == 200:
                return resp.text
            print(f"    HTTP {resp.status_code}，稍後重試")
        except requests.RequestException as e:
            print(f"    連線失敗（{str(e)[:40]}），稍後重試")
        time.sleep(5 * (attempt + 1))
    return None


def search_board(board, query, max_pages):
    results = []
    for page in range(1, max_pages + 1):
        html = get(f"{BASE}/bbs/{board}/search", params={"q": query, "page": page})
        time.sleep(DELAY)
        if not html:
            break
        entries = BeautifulSoup(html, "html.parser").select("div.r-ent")
        if not entries:
            break
        for e in entries:
            a = e.select_one("div.title a")
            if a:  # 已刪除的文章沒有連結
                results.append({"標題": a.get_text(strip=True), "url": BASE + a["href"]})
    return results


def article_type(title):
    m = re.search(r"\[(.+?)\]", str(title))
    tag = m.group(1).strip() if m else ""
    for t, tags in TYPE_MAP.items():
        if tag in tags:
            return t
    return "其他"


def parse_time(text):
    try:
        return datetime.strptime(text.strip(), "%a %b %d %H:%M:%S %Y").strftime("%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return None


def parse_article(html):
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("#main-content")
    if main is None:
        return None

    meta = {}
    for m in main.select("div.article-metaline"):
        k, v = m.select_one(".article-meta-tag"), m.select_one(".article-meta-value")
        if k and v:
            meta[k.get_text(strip=True)] = v.get_text(strip=True)

    pushes = []
    for p in main.select("div.push"):
        tag = p.select_one(".push-tag")
        if tag is None:
            continue
        user = p.select_one(".push-userid")
        content = p.select_one(".push-content")
        when = p.select_one(".push-ipdatetime")
        pushes.append({
            "標記": tag.get_text(strip=True),
            "留言者": user.get_text(strip=True) if user else "",
            "留言內容": content.get_text().lstrip(":").strip() if content else "",
            "留言時間": when.get_text(strip=True) if when else "",
        })

    # 拿掉標頭與推文，剩下的就是內文；再切掉簽名檔與發信站資訊
    for el in main.select("div.article-metaline, div.article-metaline-right, div.push"):
        el.decompose()
    body = main.get_text()
    body = body.split("※ 發信站")[0]
    body = re.split(r"\n--\n", body)[0].strip()

    return {
        "作者": meta.get("作者", ""),
        "原始標題": meta.get("標題", ""),
        "日期": parse_time(meta.get("時間", "")),
        "內文": body,
        "推文": pushes,
    }


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def load_article_cache():
    cache = {}
    if os.path.exists(ARTICLE_CACHE):
        with open(ARTICLE_CACHE, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                cache[rec["url"]] = rec
    return cache


def main():
    # 1) 搜尋：每一批 x 品牌 x 看板 x 關鍵字
    search_cache = load_json(SEARCH_CACHE, {})
    hits = []  # (品牌, 看板, 標題, url, 批次)
    for run in RUNS:
        print(f"\n=== {run['名稱']} ===")
        for brand in run["brands"]:
            for board in run["boards"]:
                for q in BRAND_QUERIES[brand]:
                    # 第一批沿用舊的快取名稱，才能直接讀到之前抓過的結果
                    key = f"{board}|{q}" if run["max_pages"] == 3 else f"{board}|{q}|p{run['max_pages']}"
                    if key not in search_cache:
                        print(f"搜尋 {board} 板：{q}（最多 {run['max_pages']} 頁）")
                        search_cache[key] = search_board(board, q, run["max_pages"])
                        with open(SEARCH_CACHE, "w", encoding="utf-8") as f:
                            json.dump(search_cache, f, ensure_ascii=False)
                    for r in search_cache[key]:
                        if run["skip_title"] and re.search(run["skip_title"], r["標題"]):
                            continue
                        hits.append((brand, board, r["標題"], r["url"], run["名稱"]))

    hits_df = pd.DataFrame(hits, columns=["品牌", "看板", "標題", "url", "批次"])
    fp = hits_df["標題"].str.contains(FALSE_POSITIVE, case=False, regex=True)
    print(f"\n排除搜錯的文章：{fp.sum()} 筆")
    hits_df = hits_df[~fp].drop_duplicates(subset=["品牌", "url"])
    print(f"搜尋完成：{hits_df['url'].nunique()} 篇不重複文章")

    # 2) 抓文章內容（有快取就跳過）
    cache = load_article_cache()
    todo = [u for u in hits_df["url"].unique() if u not in cache]
    print(f"已抓過 {len(cache)} 篇，這次要抓 {len(todo)} 篇")
    with open(ARTICLE_CACHE, "a", encoding="utf-8") as f:
        for i, url in enumerate(todo, 1):
            html = get(url)
            time.sleep(DELAY)
            art = parse_article(html) if html else None
            rec = {"url": url, **(art or {"作者": "", "原始標題": "", "日期": None,
                                          "內文": "", "推文": [], "失敗": True})}
            cache[url] = rec
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if i % 20 == 0 or i == len(todo):
                print(f"  進度 {i}/{len(todo)}")

    # 3) 整理成兩張表
    art_rows, cmt_rows = [], []
    for _, h in hits_df.iterrows():
        rec = cache.get(h["url"])
        if not rec or rec.get("失敗"):
            continue
        tags = [p["標記"] for p in rec["推文"]]
        art_rows.append({
            "品牌": h["品牌"], "看板": h["看板"], "標題": h["標題"],
            "文章類型": article_type(h["標題"]),
            "作者": rec["作者"], "日期": rec["日期"], "url": h["url"],
            "內文": rec["內文"],
            "推數": tags.count("推"), "噓數": tags.count("噓"), "箭頭數": tags.count("→"),
        })
        for p in rec["推文"]:
            cmt_rows.append({"品牌": h["品牌"], "看板": h["看板"], "url": h["url"],
                             "文章類型": article_type(h["標題"]), **p})

    articles = pd.DataFrame(art_rows)
    comments = pd.DataFrame(cmt_rows)
    articles.to_csv("ptt_articles.csv", index=False, encoding="utf-8-sig")
    comments.to_csv("ptt_comments.csv", index=False, encoding="utf-8-sig")

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)
    print("\n各品牌 x 文章類型 文章數：")
    print(pd.crosstab(articles["品牌"], articles["文章類型"], margins=True, margins_name="合計"))
    exp = comments[comments["文章類型"].isin(["心得食記", "討論", "新聞"])]
    print("\n體驗類文章（心得食記、討論、新聞）的推文標記：")
    print(pd.crosstab(exp["品牌"], exp["標記"], margins=True, margins_name="合計"))
    print(f"\n已儲存 ptt_articles.csv（{len(articles)} 篇）、ptt_comments.csv（{len(comments)} 則）")


if __name__ == "__main__":
    main()