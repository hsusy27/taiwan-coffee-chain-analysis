"""
咖啡品牌分析 - Step 4: 加入鄉鎮市區人口，計算「每萬人門市數」

資料來源：內政部戶政司「各鄉鎮市區人口密度」（政府資料開放平台資料集 8410）
欄位：統計年(statistic_yyy)、區域別(site_id，例如「臺北市大安區」)、
      人口數(people_total)、土地面積(area)、人口密度(population_density)

取得方式（程式會自動依序嘗試）：
1. 呼叫戶政司 Open API（不用金鑰）
2. API 失敗的話，讀取你手動下載的 population.csv
   手動下載：到 data.gov.tw 搜尋「各鄉鎮市區人口密度」，下載最新年度 CSV，
   改名為 population.csv 放在這個資料夾

輸入：stores_clean.csv（上一步產出）
輸出：
    town_metrics.csv        每個鄉鎮市區一列：人口、面積、各品牌門市數、每萬人門市數
    brand_town_metrics.csv  品牌 x 鄉鎮市區（含 0 間的區域），FineBI 做品牌比較用
"""

import os

import pandas as pd
import requests

API_URL = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore/ODRP014/{year}"
YEARS_TO_TRY = [114, 113]  # 民國年，先試最新年度
LOCAL_CSV = "population.csv"
MIN_POP_FOR_RANKING = 50_000  # 排名時排除人口太少的區，避免一間店就衝上第一

# 縣市 -> 區域（跟 clean_stores.py 一致）；花東與離島不在 OSM 抓取範圍內，標成「未涵蓋」
REGION_MAP = {
    "北部": ["臺北市", "新北市", "基隆市", "桃園市", "新竹市", "新竹縣", "宜蘭縣"],
    "中部": ["苗栗縣", "臺中市", "彰化縣", "南投縣", "雲林縣"],
    "南部": ["嘉義市", "嘉義縣", "臺南市", "高雄市", "屏東縣", "澎湖縣"],
    "未涵蓋": ["花蓮縣", "臺東縣", "金門縣", "連江縣"],
}
COUNTY_TO_REGION = {c: r for r, cs in REGION_MAP.items() for c in cs}


def fetch_api() -> pd.DataFrame | None:
    for year in YEARS_TO_TRY:
        url = API_URL.format(year=year)
        rows, page = [], 1
        try:
            while True:
                resp = requests.get(url, params={"page": page}, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                rows.extend(data.get("responseData") or [])
                if page >= int(data.get("totalPage") or 1):
                    break
                page += 1
        except (requests.RequestException, ValueError) as e:
            print(f"  API（民國 {year} 年）失敗：{str(e)[:60]}")
            continue
        if rows:
            print(f"  API 取得民國 {year} 年資料，共 {len(rows)} 筆")
            return pd.DataFrame(rows)
    return None


def load_local() -> pd.DataFrame | None:
    if not os.path.exists(LOCAL_CSV):
        return None
    for enc in ["utf-8-sig", "big5"]:
        try:
            df = pd.read_csv(LOCAL_CSV, dtype=str, encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    # 戶政司 CSV 第二列常是中文欄位說明（例如「區域別」），把它拿掉
    df = df[df["site_id"] != "區域別"]
    print(f"  讀取本機 {LOCAL_CSV}，共 {len(df)} 筆")
    return df


def load_population() -> pd.DataFrame:
    print("取得人口資料...")
    df = fetch_api()
    if df is None:
        df = load_local()
    if df is None:
        raise RuntimeError(
            "API 失敗，也找不到 population.csv。請到 data.gov.tw 搜尋"
            "「各鄉鎮市區人口密度」下載 CSV，改名 population.csv 放進資料夾。"
        )
    df = df.rename(columns={
        "site_id": "區域別", "people_total": "人口數",
        "area": "面積_平方公里", "population_density": "人口密度",
    })
    # 統一寫法：去空白、台 -> 臺，才能跟行政區圖資對上
    df["區域別"] = (
        df["區域別"].astype(str).str.replace(r"\s", "", regex=True)
        .str.replace("台", "臺")
    )
    for col in ["人口數", "面積_平方公里", "人口密度"]:
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", ""), errors="coerce")
    return df[["區域別", "人口數", "面積_平方公里", "人口密度"]].dropna(subset=["人口數"])


def main():
    stores = pd.read_csv("stores_clean.csv")
    stores["區域別"] = stores["縣市"] + stores["鄉鎮市區"]
    pop = load_population()

    # 檢查：有門市的區域是否都找得到人口
    missing = sorted(set(stores["區域別"]) - set(pop["區域別"]))
    if missing:
        print(f"\n注意：{len(missing)} 個有門市的區域找不到人口資料：{missing[:10]}")

    # 每個鄉鎮市區的基本資訊（以人口資料為主，沒有門市的區也保留，才看得出空白市場）
    towns = pop.copy()
    town_info = stores[["區域別", "區域", "縣市", "鄉鎮市區"]].drop_duplicates("區域別")
    towns = towns.merge(town_info, on="區域別", how="left")
    # 沒有門市的區，從區域別拆出縣市與鄉鎮名稱
    no_info = towns["縣市"].isna()
    towns.loc[no_info, "縣市"] = towns.loc[no_info, "區域別"].str[:3]
    towns.loc[no_info, "鄉鎮市區"] = towns.loc[no_info, "區域別"].str[3:]
    towns["區域"] = towns["縣市"].map(COUNTY_TO_REGION).fillna("未涵蓋")

    # 寬表：每區一列，各品牌一欄
    counts = stores.pivot_table(index="區域別", columns="品牌", aggfunc="size", fill_value=0)
    brands = list(counts.columns)
    towns = towns.merge(counts, left_on="區域別", right_index=True, how="left")
    towns[brands] = towns[brands].fillna(0).astype(int)
    towns["門市總數"] = towns[brands].sum(axis=1)
    towns["每萬人門市數"] = (towns["門市總數"] / towns["人口數"] * 10_000).round(3)
    for b in brands:
        towns[f"{b}_每萬人"] = (towns[b] / towns["人口數"] * 10_000).round(3)

    lead = ["區域", "縣市", "鄉鎮市區", "區域別", "人口數", "面積_平方公里", "人口密度"]
    towns = towns[lead + ["門市總數", "每萬人門市數"] + brands
                  + [f"{b}_每萬人" for b in brands]]
    towns.to_csv("town_metrics.csv", index=False, encoding="utf-8-sig")

    # 長表：品牌 x 區（含 0 間），FineBI 拖拉比較品牌最方便
    long = towns.melt(
        id_vars=lead, value_vars=brands, var_name="品牌", value_name="門市數"
    )
    long["每萬人門市數"] = (long["門市數"] / long["人口數"] * 10_000).round(3)
    long.to_csv("brand_town_metrics.csv", index=False, encoding="utf-8-sig")

    # 終端機上的快速洞察
    covered = towns[towns["區域"] != "未涵蓋"]
    big = covered[covered["人口數"] >= MIN_POP_FOR_RANKING]
    show = ["區域別", "人口數", "門市總數", "每萬人門市數"]
    print(f"\n每萬人門市數最高（人口 {MIN_POP_FOR_RANKING:,} 以上）：")
    print(big.nlargest(10, "每萬人門市數")[show].to_string(index=False))
    print("\n人口最多卻沒有任何一間的區（潛在空白市場）：")
    empty = big[big["門市總數"] == 0]
    if empty.empty:
        print("  （人口 5 萬以上的區都至少有一間）")
    else:
        print(empty.nlargest(10, "人口數")[show].to_string(index=False))

    print("\n已儲存 town_metrics.csv、brand_town_metrics.csv")


if __name__ == "__main__":
    main()