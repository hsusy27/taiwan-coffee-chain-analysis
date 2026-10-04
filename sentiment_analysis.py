"""
咖啡品牌分析 - Step 7: PTT 留言情緒分析與主題分析

做的事：
1. 把文章分成兩類分析：
   - 促銷反應：優惠情報文（看網友對各品牌優惠買不買單）
   - 體驗口碑：其他所有文章（心得、討論、新聞），才拿來算品牌口碑
2. 用 lexicon.py 的詞典判斷每則留言的正負面，並標記提到的主題
3. 產出 FineBI 用的各種彙整表
4. 抽樣產生人工標註表，用來驗證詞典的準確率

安裝套件：
    pip install jieba pandas

需要的檔案（同一個資料夾）：
    ptt_articles.csv、ptt_comments.csv、lexicon.py

執行：
    python sentiment_analysis.py
    （標註完 labeling_sample.csv 後再執行一次，會自動算出準確率）
"""

import collections
import logging
import os
import re

import jieba
import pandas as pd

import lexicon as L

jieba.setLogLevel(logging.WARNING)

MIN_OPINIONS = 100        # 有表態的留言少於這個數，標記為「樣本不足」
MIN_TOPIC_OPINIONS = 10   # 主題內有表態的留言少於這個數，不計算主題淨情緒（避免 1 則就變 -100）
LABEL_FILE = "labeling_sample.csv"
LABEL_N_OPINION = 70      # 抽樣：詞典有判斷的留言
LABEL_N_NEUTRAL = 30      # 抽樣：詞典判為中性的留言（檢查漏判）

PROMO_TAGS = ["情報", "省錢", "優惠", "廣宣", "商品"]
FALSE_POSITIVE = r"moccamaster|pacamara"

# 文字雲用的停用詞：太常見、沒有資訊量的詞
STOPWORDS = set("""
可以 真的 不是 就是 覺得 沒有 知道 還是 看到 自己 這個 應該 什麼 一樣 之前 還有
今天 只有 不會 現在 不要 不能 怎麼 很多 一杯 一下 直接 感覺 問題 請問 一堆 好像
這樣 大家 所以 我覺 不用 樓上 他們 有人 其實 一個 本來 有點 一次 因為 如果 不過
這麼 那個 昨天 咖啡 分享 謝謝 感謝 原po 推文 八卦 是不是 到底 已經 開始 結果 時候
可是 看看 只是 而且 還要 以為 那種 這種 一定 根本 比較 為什麼 還是 不然 然後 一直
一送 送一 買一 第二 半價
""".split())

# 品牌名稱本身也排除：每個品牌的文字雲都一定會出現自己的名字，沒有資訊量
STOPWORDS |= {"星巴克", "路易莎", "丹堤", "怡客", "cama", "Cama", "統一", "咖啡廳", "咖啡店"}


def tag_of(title):
    m = re.search(r"\[(.+?)\]", str(title))
    return m.group(1).strip() if m else ""


def label_of(score):
    return "正面" if score > 0 else "負面" if score < 0 else "中性"


def net_index(pos, neg):
    """淨情緒指數：-100（全負面）到 100（全正面）"""
    total = pos + neg
    return round((pos - neg) / total * 100, 1) if total else None


def load_data():
    arts = pd.read_csv("ptt_articles.csv")
    cmts = pd.read_csv("ptt_comments.csv")

    arts = arts[~arts["標題"].str.contains(FALSE_POSITIVE, case=False, regex=True, na=False)]
    arts["年"] = pd.to_datetime(arts["日期"], errors="coerce").dt.year
    arts["分析類別"] = arts["標題"].map(
        lambda t: "促銷反應" if tag_of(t) in PROMO_TAGS else "體驗口碑"
    )

    meta = arts[["品牌", "url", "標題", "年", "分析類別"]]
    cmts = cmts.drop(columns=[c for c in ["文章類型"] if c in cmts.columns])
    cmts = cmts.merge(meta, on=["品牌", "url"], how="inner")  # 同時排除搜錯文章的留言
    cmts["留言內容"] = cmts["留言內容"].fillna("").astype(str)
    return arts, cmts


def score_comments(cmts):
    result = cmts["留言內容"].map(L.sentiment_score)
    cmts["情緒分數"] = result.map(lambda r: r[0])
    cmts["命中詞"] = result.map(lambda r: "、".join(r[1]))
    cmts["情緒"] = cmts["情緒分數"].map(label_of)
    cmts["主題"] = cmts["留言內容"].map(L.find_topics)
    return cmts


def brand_sentiment(cmts):
    g = cmts.groupby(["品牌", "分析類別"])
    out = pd.DataFrame({
        "留言數": g.size(),
        "正面": g["情緒"].apply(lambda s: (s == "正面").sum()),
        "負面": g["情緒"].apply(lambda s: (s == "負面").sum()),
    }).reset_index()
    out["有表態留言"] = out["正面"] + out["負面"]
    out["表態率%"] = (out["有表態留言"] / out["留言數"] * 100).round(1)
    out["淨情緒指數"] = [net_index(p, n) for p, n in zip(out["正面"], out["負面"])]
    out["樣本"] = out["有表態留言"].map(lambda n: "足夠" if n >= MIN_OPINIONS else "樣本不足")
    return out


def brand_topic(exp):
    rows = []
    for brand, g in exp.groupby("品牌"):
        total = len(g)
        for topic in L.TOPICS:
            t = g[g["主題"].map(lambda ts: topic in ts)]
            pos, neg = (t["情緒"] == "正面").sum(), (t["情緒"] == "負面").sum()
            rows.append({
                "品牌": brand, "主題": topic, "提及數": len(t),
                "提及率%": round(len(t) / total * 100, 2) if total else 0,
                "正面": pos, "負面": neg,
                "主題淨情緒": net_index(pos, neg) if pos + neg >= MIN_TOPIC_OPINIONS else None,
            })
    return pd.DataFrame(rows)


def brand_year(exp):
    g = exp.groupby(["品牌", "年"])
    out = pd.DataFrame({
        "留言數": g.size(),
        "正面": g["情緒"].apply(lambda s: (s == "正面").sum()),
        "負面": g["情緒"].apply(lambda s: (s == "負面").sum()),
        "爭議留言數": g["主題"].apply(lambda s: s.map(lambda ts: "爭議與抵制" in ts).sum()),
    }).reset_index()
    out["淨情緒指數"] = [net_index(p, n) for p, n in zip(out["正面"], out["負面"])]
    return out


def promo_reaction(arts):
    p = arts[arts["分析類別"] == "促銷反應"].groupby("品牌")[["推數", "噓數"]].sum()
    p["優惠文數"] = arts[arts["分析類別"] == "促銷反應"].groupby("品牌").size()
    p["噓比例%"] = (p["噓數"] / (p["推數"] + p["噓數"]) * 100).round(1)
    return p.reset_index()


def brand_keywords(exp, top_n=50):
    rows = []
    for brand, g in exp.groupby("品牌"):
        cnt = collections.Counter(
            w for text in g["留言內容"] for w in jieba.lcut(text)
            if len(w) > 1 and w not in STOPWORDS
            and not re.fullmatch(r"[\W\d_a-zA-Z]+", w)
        )
        rows += [{"品牌": brand, "詞": w, "次數": n} for w, n in cnt.most_common(top_n)]
    return pd.DataFrame(rows)


def make_or_evaluate_labels(exp):
    if os.path.exists(LABEL_FILE):
        lab = pd.read_csv(LABEL_FILE)
        done = lab["人工標註"].isin(["正面", "負面", "中性"])
        if done.sum() == 0:
            print(f"\n{LABEL_FILE} 已存在但還沒標註，請填寫「人工標註」欄（正面／負面／中性）")
            return
        lab = lab[done]
        acc = (lab["詞典判斷"] == lab["人工標註"]).mean()
        judged = lab[lab["詞典判斷"] != "中性"]
        precision = (judged["詞典判斷"] == judged["人工標註"]).mean() if len(judged) else float("nan")
        print(f"\n=== 詞典驗證（已標註 {len(lab)} 則）===")
        print(f"整體準確率：{acc:.1%}")
        print(f"詞典有表態時的正確率：{precision:.1%}（{len(judged)} 則）")
        print(pd.crosstab(lab["人工標註"], lab["詞典判斷"],
                          rownames=["人工"], colnames=["詞典"]))
        return

    # 第一次執行：抽樣產生標註表（不會覆蓋已存在的檔案，避免洗掉你的標註）
    has = exp[exp["情緒"] != "中性"]
    none = exp[(exp["情緒"] == "中性") & (exp["留言內容"].str.len() >= 5)]
    sample = pd.concat([
        has.sample(min(LABEL_N_OPINION, len(has)), random_state=42),
        none.sample(min(LABEL_N_NEUTRAL, len(none)), random_state=42),
    ]).sample(frac=1, random_state=42)  # 打亂順序，標註時才不會受詞典判斷影響
    sample = sample[["品牌", "留言內容", "情緒"]].rename(columns={"情緒": "詞典判斷"})
    sample["人工標註"] = ""
    sample.to_csv(LABEL_FILE, index=False, encoding="utf-8-sig")
    print(f"\n已產生 {LABEL_FILE}（{len(sample)} 則），請用 Excel 打開，")
    print("在「人工標註」欄填入：正面／負面／中性（判斷對品牌的態度），存檔後再執行一次本程式。")


def main():
    arts, cmts = load_data()
    cmts = score_comments(cmts)
    exp = cmts[cmts["分析類別"] == "體驗口碑"].copy()

    out = cmts.copy()
    out["主題"] = out["主題"].map("、".join)
    out.to_csv("comments_scored.csv", index=False, encoding="utf-8-sig")

    bs = brand_sentiment(cmts)
    bt = brand_topic(exp)
    by = brand_year(exp)
    pr = promo_reaction(arts)
    kw = brand_keywords(exp)
    bs.to_csv("brand_sentiment.csv", index=False, encoding="utf-8-sig")
    bt.to_csv("brand_topic.csv", index=False, encoding="utf-8-sig")
    by.to_csv("brand_year.csv", index=False, encoding="utf-8-sig")
    pr.to_csv("promo_reaction.csv", index=False, encoding="utf-8-sig")
    kw.to_csv("brand_keywords.csv", index=False, encoding="utf-8-sig")

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)
    print("=== 品牌情緒（體驗口碑 vs 促銷反應）===")
    print(bs.to_string(index=False))
    print("\n=== 體驗口碑：各主題提及率%（雷達圖用）===")
    print(bt.pivot(index="品牌", columns="主題", values="提及率%").to_string())
    print("\n=== 體驗口碑：各主題淨情緒 ===")
    print(bt.pivot(index="品牌", columns="主題", values="主題淨情緒").to_string())
    print("\n=== 促銷反應 ===")
    print(pr.to_string(index=False))

    make_or_evaluate_labels(exp)
    print("\n已儲存 comments_scored.csv、brand_sentiment.csv、brand_topic.csv、"
          "brand_year.csv、promo_reaction.csv、brand_keywords.csv")


if __name__ == "__main__":
    main()