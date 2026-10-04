"""
咖啡品牌口碑分析 - Step 1: 從「全國營業(稅籍)登記資料集」篩選咖啡連鎖品牌

事前準備：
1. 下載 https://eip.fia.gov.tw/data/BGMOPEN1.zip （不用解壓縮）
2. 放在這支腳本同一個資料夾，檔名維持 BGMOPEN1.zip

安裝套件：
    pip install pandas
"""

import pandas as pd

INPUT_FILE = "BGMOPEN1.zip"  # 若你已解壓縮，改成 BGMOPEN1.csv 即可
CHUNK_SIZE = 200_000  # 檔案很大，分批讀取避免記憶體不足

# 用「營業人名稱」比對品牌。關鍵字是猜的，請看跑完印出的名稱清單再調整
BRAND_KEYWORDS = {
    "星巴克": ["星巴克"],
    "路易莎": ["路易莎"],
    "Cama": ["cama", "卡瑪"],
    "怡客咖啡": ["怡客"],
    "丹堤咖啡": ["丹堤"],
}


def roc_to_date(s):
    """民國日期(例如 1141226)轉成西元日期"""
    s = str(s).strip()
    if not s.isdigit() or len(s) < 6:
        return pd.NaT
    try:
        return pd.Timestamp(int(s[:-4]) + 1911, int(s[-4:-2]), int(s[-2:]))
    except ValueError:
        return pd.NaT


def match_brand(name):
    if not isinstance(name, str):
        return None
    low = name.lower()
    for brand, kws in BRAND_KEYWORDS.items():
        if any(k.lower() in low for k in kws):
            return brand
    return None


def main():
    hits = []
    latest_setup = 0
    total_rows = 0

    for chunk in pd.read_csv(INPUT_FILE, dtype=str, chunksize=CHUNK_SIZE,
                             encoding="utf-8-sig"):
        total_rows += len(chunk)
        latest_setup = max(
            latest_setup,
            pd.to_numeric(chunk["設立日期"], errors="coerce").max() or 0,
        )
        chunk["品牌"] = chunk["營業人名稱"].apply(match_brand)
        hits.append(chunk[chunk["品牌"].notna()])

    df = pd.concat(hits, ignore_index=True)
    df["設立日期_西元"] = df["設立日期"].apply(roc_to_date)
    df["設立年"] = df["設立日期_西元"].dt.year
    df["資本額"] = pd.to_numeric(df["資本額"], errors="coerce")

    print(f"全檔總筆數：{total_rows:,}")
    print(f"檔案中最新的設立日期(民國)：{int(latest_setup)}  <- 用來確認資料新不新")
    print("\n各品牌篩選筆數：")
    print(df["品牌"].value_counts())

    print("\n各品牌對應到的營業人名稱（前10，請檢查有沒有誤判或漏掉）：")
    for brand, g in df.groupby("品牌"):
        print(f"\n[{brand}]")
        print(g["營業人名稱"].value_counts().head(10).to_string())

    df.to_csv("coffee_brands_filtered.csv", index=False, encoding="utf-8-sig")
    print(f"\n已儲存 coffee_brands_filtered.csv（{len(df)} 筆）")


if __name__ == "__main__":
    main()