"""
咖啡品牌分析 - Step 5: 路易莎展店趨勢（稅籍登記資料）

做的事：
1. 從 coffee_brands_filtered.csv 篩出路易莎的「門市分公司」
   （排除工廠、總部，以及名稱剛好有「路易莎」的無關商號）
2. 地址全形轉半形，對照 town_metrics.csv 的官方鄉鎮名稱，找出縣市與鄉鎮市區
3. 統計每年新設立門市數、累計數，以及各區域占比的變化

輸入：coffee_brands_filtered.csv、town_metrics.csv（前面步驟產出）
輸出：
    louisa_branches.csv            每間分公司一列（設立日期、縣市、鄉鎮、區域）
    louisa_trend_year.csv          每年新設立數與累計數
    louisa_trend_year_region.csv   每年 x 區域 的新設立數（看展店重心有沒有往中南部移）

重要限制（請寫進報告）：
稅籍資料只包含「目前仍在營業」的單位，已關閉的門市不在裡面。
所以這裡的「每年新設立數」其實是「現存門市的設立年份分布」，越早期的年份越可能被低估。
"""

import unicodedata

import pandas as pd

COMPANY = "路易莎職人咖啡股份有限公司"
EXCLUDE_KEYWORDS = ["廠", "總部"]

REGION_MAP = {
    "北部": ["臺北市", "新北市", "基隆市", "桃園市", "新竹市", "新竹縣", "宜蘭縣"],
    "中部": ["苗栗縣", "臺中市", "彰化縣", "南投縣", "雲林縣"],
    "南部": ["嘉義市", "嘉義縣", "臺南市", "高雄市", "屏東縣", "澎湖縣"],
    "東部": ["花蓮縣", "臺東縣"],
    "離島": ["金門縣", "連江縣"],
}
COUNTY_TO_REGION = {c: r for r, cs in REGION_MAP.items() for c in cs}

# 縣市升格前的舊名稱，地址若還是舊寫法就換成新名稱
OLD_COUNTY = {"臺北縣": "新北市", "桃園縣": "桃園市", "臺中縣": "臺中市",
              "臺南縣": "臺南市", "高雄縣": "高雄市"}


def normalize_address(addr: str) -> str:
    s = unicodedata.normalize("NFKC", str(addr))  # 全形數字、全形空白 -> 半形
    s = "".join(s.split()).replace("台", "臺")
    for old, new in OLD_COUNTY.items():
        s = s.replace(old, new)
    return s


def build_matcher(town_names):
    # 名稱長的先比對，避免「新市區」被「新市」之類的短名稱搶先吃掉
    names = sorted(set(town_names), key=len, reverse=True)

    def match(addr: str):
        for n in names:
            if addr.startswith(n):
                return n
        return None

    return match


def main():
    df = pd.read_csv("coffee_brands_filtered.csv", dtype=str)
    towns = pd.read_csv("town_metrics.csv", dtype=str)

    lou = df[df["品牌"] == "路易莎"].copy()
    name = lou["營業人名稱"].fillna("")
    is_branch = name.str.startswith(COMPANY) & name.str.contains("分公司|營業所")
    is_excluded = name.str.contains("|".join(EXCLUDE_KEYWORDS))
    print(f"稅籍中含「路易莎」：{len(lou)} 筆")
    print(f"  非路易莎公司的商號（排除）：{(~name.str.startswith(COMPANY)).sum()} 筆")
    print(f"  工廠、總部（排除）：{(is_branch & is_excluded).sum()} 筆")
    lou = lou[is_branch & ~is_excluded].copy()
    print(f"  保留門市分公司：{len(lou)} 筆")

    lou["分店名稱"] = (
        lou["營業人名稱"].str.replace(COMPANY, "", regex=False)
        .str.replace("分公司|營業所", "", regex=True)
    )
    lou["地址_標準化"] = lou["營業地址"].apply(normalize_address)
    match = build_matcher(towns["區域別"])
    lou["區域別"] = lou["地址_標準化"].apply(match)

    miss = lou["區域別"].isna()
    if miss.any():
        print(f"\n注意：{miss.sum()} 筆地址對不到鄉鎮市區（保留，但區域標成「未知」）：")
        print(lou.loc[miss, "營業地址"].head(5).to_string(index=False))

    lou["縣市"] = lou["區域別"].str[:3]
    lou["鄉鎮市區"] = lou["區域別"].str[3:]
    lou["區域"] = lou["縣市"].map(COUNTY_TO_REGION).fillna("未知")
    lou["設立日期"] = pd.to_datetime(lou["設立日期_西元"], errors="coerce")
    lou["設立年"] = lou["設立日期"].dt.year
    lou["資本額"] = pd.to_numeric(lou["資本額"], errors="coerce")

    latest = lou["設立日期"].max()
    print(f"\n資料最新設立日期：{latest:%Y-%m-%d}（{latest.year} 年為部分年度）")

    out = lou[["統一編號", "分店名稱", "設立日期", "設立年", "區域", "縣市",
               "鄉鎮市區", "營業地址", "資本額"]].sort_values("設立日期")
    out.to_csv("louisa_branches.csv", index=False, encoding="utf-8-sig")

    # 每年新設立 + 累計
    yearly = out.groupby("設立年").size().rename("新設立數").to_frame()
    yearly = yearly.reindex(range(int(yearly.index.min()), latest.year + 1), fill_value=0)
    yearly["累計門市數"] = yearly["新設立數"].cumsum()
    yearly["是否完整年度"] = ["否" if y == latest.year else "是" for y in yearly.index]
    yearly.index.name = "設立年"
    yearly.reset_index().to_csv("louisa_trend_year.csv", index=False, encoding="utf-8-sig")

    # 每年 x 區域
    by_region = out.groupby(["設立年", "區域"]).size().reset_index(name="新設立數")
    by_region.to_csv("louisa_trend_year_region.csv", index=False, encoding="utf-8-sig")

    print("\n每年新設立門市（現存門市口徑）：")
    print(yearly.to_string())

    # 依時期看區域占比，觀察展店重心是否轉移
    bins = [0, 2015, 2019, 2022, 9999]
    labels = ["2015以前", "2016-2019", "2020-2022", "2023以後"]
    out["時期"] = pd.cut(out["設立年"], bins=bins, labels=labels)
    share = pd.crosstab(out["時期"], out["區域"], normalize="index").mul(100).round(1)
    print("\n各時期新設門市的區域占比（%）：")
    print(share.to_string())
    print("\n已儲存 louisa_branches.csv、louisa_trend_year.csv、louisa_trend_year_region.csv")


if __name__ == "__main__":
    main()