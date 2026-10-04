"""
咖啡品牌口碑分析 - OpenStreetMap Overpass 抓取腳本（分區、可續傳版）

改良重點：
1. 把台灣切成 5 個小區域分開查，每次查詢很輕，不容易 504
2. 用範圍框(bbox)取代整個國家的區域運算，伺服器負擔小很多
3. 每個區域抓完就存檔，中途失敗重跑時會自動跳過已完成的區域
4. 單一區域失敗不會讓整支程式中斷

安裝套件：
    pip install requests pandas

執行：
    python fetch_osm_coffee.py
    （若有區域失敗，過幾分鐘再執行一次，會只補抓失敗的區域）
"""

import json
import os
import re
import time

import pandas as pd
import requests

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
]

# 品牌 -> 比對用正規表示式（Python 端不分大小寫）
BRANDS = {
    "星巴克": r"星巴克|starbucks",
    "路易莎": r"路易莎|louisa",
    "Cama": r"(^|[^a-z])cama([^a-z]|$)",
    "怡客咖啡": r"怡客|ikari",
    "丹堤咖啡": r"丹堤|dante",
}

# 送給 Overpass 的版本：不用不分大小寫旗標（那樣很慢），改列出常見大小寫
QUERY_REGEX = (
    "星巴克|Starbucks|STARBUCKS|路易莎|Louisa|LOUISA|"
    "^(cama|Cama|CAMA)([^a-zA-Z]|$)|怡客|Ikari|IKARI|丹堤|Dante|DANTE"
)

# 台灣切成 10 個小區：(名稱, 南, 西, 北, 東)
# 範圍越小，伺服器越不容易逾時
TILES = [
    ("屏東高雄南", 21.8, 120.0, 22.55, 121.0),
    ("高雄北台南南", 22.55, 120.0, 23.0, 121.0),
    ("台南嘉義", 23.0, 119.3, 23.6, 120.7),
    ("雲林彰化南", 23.6, 119.3, 24.0, 120.7),
    ("台中彰化北", 24.0, 120.3, 24.4, 121.0),
    ("苗栗新竹", 24.4, 120.6, 24.9, 121.3),
    ("東部花東", 22.0, 121.0, 24.4, 122.1),
    ("桃園", 24.9, 120.9, 25.15, 121.35),
    ("雙北西側", 24.9, 121.35, 25.35, 121.5),
    ("雙北東側基隆宜蘭", 24.4, 121.5, 25.35, 122.1),
]

CACHE_DIR = "osm_cache_v2"  # 換新資料夾，避免讀到舊的 0 筆存檔
CLIENT_TIMEOUT = 150


def build_query(s, w, n, e) -> str:
    stmts = [
        f'  nwr["amenity"="cafe"]["name"~"{QUERY_REGEX}"];',
        f'  nwr["amenity"="cafe"]["name:en"~"{QUERY_REGEX}"];',
        f'  nwr["amenity"="cafe"]["brand"~"{QUERY_REGEX}"];',
        f'  nwr["amenity"="fast_food"]["name"~"{QUERY_REGEX}"];',
    ]
    return (
        f"[out:json][timeout:90][bbox:{s},{w},{n},{e}];\n"
        "(\n" + "\n".join(stmts) + "\n);\n"
        "out center tags;"
    )


def fetch_tile(name, bbox) -> list | None:
    query = build_query(*bbox)
    headers = {"User-Agent": "coffee-bi-portfolio/1.0 (student project)"}
    for url in ENDPOINTS:
        try:
            print(f"  連線 {url.split('/')[2]} ...", end=" ")
            resp = requests.post(
                url, data={"data": query}, headers=headers, timeout=CLIENT_TIMEOUT
            )
            if resp.status_code == 200:
                payload = resp.json()
                remark = payload.get("remark", "")
                # Overpass 逾時時常回 200 + remark 錯誤訊息 + 0 筆，這種要當成失敗
                if "error" in remark.lower() or "timed out" in remark.lower():
                    print(f"伺服器逾時（{remark[:50]}）")
                    time.sleep(5)
                    continue
                els = payload.get("elements", [])
                print(f"成功（{len(els)} 筆）")
                return els
            print(f"HTTP {resp.status_code}")
        except requests.RequestException as e:
            print(f"失敗（{str(e)[:40]}）")
        time.sleep(5)
    return None


def classify(tags: dict):
    text = " ".join(
        tags.get(k, "")
        for k in ["name", "name:zh", "name:en", "brand", "brand:zh", "brand:en"]
    )
    for brand, rx in BRANDS.items():
        if re.search(rx, text, flags=re.IGNORECASE):
            return brand
    return None


def to_row(el, brand) -> dict:
    t = el.get("tags", {})
    return {
        "品牌": brand,
        "店名": t.get("name", ""),
        "緯度": el.get("lat") or el.get("center", {}).get("lat"),
        "經度": el.get("lon") or el.get("center", {}).get("lon"),
        "類型": t.get("amenity", ""),
        "縣市": t.get("addr:city", ""),
        "行政區": t.get("addr:district", ""),
        "街道": t.get("addr:street", ""),
        "完整地址": t.get("addr:full", ""),
        "營業時間": t.get("opening_hours", ""),
        "osm_type": el.get("type"),
        "osm_id": el.get("id"),
    }


def main():
    os.makedirs(CACHE_DIR, exist_ok=True)
    all_elements, failed = [], []

    for i, (name, s, w, n, e) in enumerate(TILES):
        cache_file = os.path.join(CACHE_DIR, f"tile_{i}.json")
        print(f"[{i + 1}/{len(TILES)}] 區域：{name}")
        if os.path.exists(cache_file):
            with open(cache_file, encoding="utf-8") as f:
                els = json.load(f)
            print(f"  已有存檔，跳過（{len(els)} 筆）")
        else:
            els = fetch_tile(name, (s, w, n, e))
            if els is None:
                print("  這個區域抓取失敗，稍後重跑會再補抓")
                failed.append(name)
                continue
            if els:  # 0 筆不存檔，下次會再試一次
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(els, f, ensure_ascii=False)
            time.sleep(8)  # 對公用伺服器客氣一點
        all_elements.extend(els)

    rows = []
    for el in all_elements:
        brand = classify(el.get("tags", {}))
        if brand:
            rows.append(to_row(el, brand))

    if not rows:
        print("\n沒有任何資料，請稍後再執行一次。")
        return

    df = pd.DataFrame(rows).dropna(subset=["緯度", "經度"])
    df = df.drop_duplicates(subset=["osm_type", "osm_id"])

    print("\n各品牌門市數（OSM 收錄）：")
    print(df["品牌"].value_counts())
    print(f"有填縣市欄位的比例：{(df['縣市'] != '').mean():.0%}")

    df.to_csv("coffee_stores_osm.csv", index=False, encoding="utf-8-sig")
    print("已儲存 coffee_stores_osm.csv")

    if failed:
        print(f"\n注意：以下區域尚未抓到，數字不完整：{'、'.join(failed)}")
        print("請過幾分鐘再執行一次補抓。")


if __name__ == "__main__":
    main()