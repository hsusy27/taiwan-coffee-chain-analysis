"""
咖啡品牌分析 - Step 8: 品牌總表（每個品牌一列）

把前面各步驟的結果合併成一張表，給儀表板第 4 頁「綜合洞察」的泡泡圖與指標卡使用。

需要的檔案（前面步驟都已產出）：
    stores_clean.csv、town_metrics.csv、brand_sentiment.csv、
    promo_reaction.csv、ptt_articles.csv

輸出：
    brand_overview.csv
"""

import pandas as pd

FALSE_POSITIVE = r"moccamaster|pacamara"


def main():
    stores = pd.read_csv("stores_clean.csv")
    towns = pd.read_csv("town_metrics.csv")
    sent = pd.read_csv("brand_sentiment.csv")
    promo = pd.read_csv("promo_reaction.csv")
    arts = pd.read_csv("ptt_articles.csv")

    # 1) 門市規模與區域分布（OSM）
    ov = stores.groupby("品牌").size().rename("門市數").to_frame()
    region = pd.crosstab(stores["品牌"], stores["區域"], normalize="index").mul(100).round(1)
    for r in ["北部", "中部", "南部"]:
        ov[f"{r}占比%"] = region[r] if r in region else 0.0
    ov["中南部占比%"] = ov["中部占比%"] + ov["南部占比%"]

    # 每萬人門市數：分母只算分析範圍內（不含花東、離島）的總人口
    covered_pop = towns.loc[towns["區域"] != "未涵蓋", "人口數"].sum()
    ov["每萬人門市數"] = (ov["門市數"] / covered_pop * 10_000).round(3)

    # 2) 體驗口碑（PTT 情緒分析）
    exp = sent[sent["分析類別"] == "體驗口碑"].set_index("品牌")
    ov["體驗淨情緒指數"] = exp["淨情緒指數"]
    ov["體驗有表態留言"] = exp["有表態留言"]
    ov["口碑樣本"] = exp["樣本"]

    # 3) 促銷反應
    ov["優惠文噓比例%"] = promo.set_index("品牌")["噓比例%"]

    # 4) PTT 討論量（泡泡大小用）
    arts = arts[~arts["標題"].str.contains(FALSE_POSITIVE, case=False, regex=True, na=False)]
    ov["PTT文章數"] = arts.groupby("品牌").size()

    # 5) 泡泡圖只放口碑樣本足夠的品牌，其他品牌仍保留在表中供指標卡使用
    ov["放入定位圖"] = ov["口碑樣本"].map(lambda s: "是" if s == "足夠" else "否")

    ov = ov.reset_index().sort_values("門市數", ascending=False)
    ov.to_csv("brand_overview.csv", index=False, encoding="utf-8-sig")

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)
    print(ov.to_string(index=False))
    print(f"\n分析範圍總人口：{covered_pop:,.0f}")
    print("已儲存 brand_overview.csv")


if __name__ == "__main__":
    main()