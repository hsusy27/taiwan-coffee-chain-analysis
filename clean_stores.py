"""
咖啡品牌分析 - Step 3: 門市資料清洗 + 用經緯度補齊縣市/行政區

做的事：
1. 用內政部「鄉鎮市區界線」圖資，依經緯度判斷每間店屬於哪個縣市、鄉鎮市區
   （不再依賴 OSM 填寫率只有一半的縣市欄位，名稱也會統一成官方寫法「臺」）
2. 移除同品牌、距離 30 公尺內的重複點位（OSM 有時同一間店同時有點和建築物）
3. 加上「區域」欄位（北、中、南、東）
4. 產出 FineBI 可以直接匯入的表格

事前準備：
1. 到 data.gov.tw 搜尋「鄉鎮市區界線(TWD97經緯度)」（資料集編號 7441），下載 SHP 壓縮檔
2. 解壓縮到 town_boundary/ 資料夾（裡面要有 .shp、.shx、.dbf 等檔案）

安裝套件：
    pip install geopandas

輸出：
    stores_clean.csv     每間店一列（FineBI 主表，用來畫地圖、篩選）
    summary_town.csv     品牌 x 縣市 x 鄉鎮市區 的門市數（長表格，FineBI 好拖拉）
"""

import glob
import os

import geopandas as gpd
import pandas as pd

INPUT_CSV = "coffee_stores_osm.csv"
BOUNDARY_DIR = "town_boundary"
DEDUP_METERS = 30
NEAREST_MAX_METERS = 500  # 落在海岸線外一點點的店，找 500 公尺內最近的行政區

REGION_MAP = {
    "北部": ["臺北市", "新北市", "基隆市", "桃園市", "新竹市", "新竹縣", "宜蘭縣"],
    "中部": ["苗栗縣", "臺中市", "彰化縣", "南投縣", "雲林縣"],
    "南部": ["嘉義市", "嘉義縣", "臺南市", "高雄市", "屏東縣", "澎湖縣"],
    "東部": ["花蓮縣", "臺東縣"],
    "離島": ["金門縣", "連江縣"],
}
COUNTY_TO_REGION = {c: r for r, cs in REGION_MAP.items() for c in cs}


def load_boundary() -> gpd.GeoDataFrame:
    shp_files = glob.glob(f"{BOUNDARY_DIR}/**/*.shp", recursive=True)
    if not shp_files:
        raise FileNotFoundError(f"在 {BOUNDARY_DIR}/ 找不到 .shp 檔，請先下載並解壓縮")
    print("資料夾內的 .shp：", [os.path.basename(f) for f in shp_files])
    # 壓縮檔裡除了全國圖層，還有個別地區的小圖層（例如瑪家三和），
    # 優先選檔名含 TOWN_MOI 的全國圖層，沒有的話選檔案最大的那個
    main_layers = [f for f in shp_files if "TOWN_MOI" in os.path.basename(f).upper()]
    path = main_layers[0] if main_layers else max(shp_files, key=os.path.getsize)
    print(f"讀取行政區圖資：{path}")
    # 政府圖資有時是 big5 編碼，先用預設讀，亂碼再改 big5
    towns = gpd.read_file(path)
    if not towns["COUNTYNAME"].astype(str).str.contains("市|縣").any():
        towns = gpd.read_file(path, encoding="big5")
    if towns.crs is None:
        towns = towns.set_crs(epsg=4326)
    return towns[["COUNTYNAME", "TOWNNAME", "TOWNCODE", "geometry"]].to_crs(epsg=3826)


def dedupe_nearby(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """同品牌、距離很近的點視為同一間店，只留一筆"""
    grid = DEDUP_METERS
    key = (
        gdf["品牌"]
        + "_" + (gdf.geometry.x // grid).astype(int).astype(str)
        + "_" + (gdf.geometry.y // grid).astype(int).astype(str)
    )
    return gdf.loc[~key.duplicated()]


def main():
    df = pd.read_csv(INPUT_CSV)
    print(f"原始門市：{len(df)} 筆")

    # TWD97 經緯度與 WGS84 幾乎相同，用 EPSG:4326 讀入，再轉成公尺單位的 EPSG:3826 計算距離
    stores = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df["經度"], df["緯度"]), crs="EPSG:4326"
    ).to_crs(epsg=3826)

    before = len(stores)
    stores = dedupe_nearby(stores)
    print(f"移除近距離重複點位：{before - len(stores)} 筆")

    towns = load_boundary()

    # 1) 點落在哪個行政區多邊形內
    joined = gpd.sjoin(stores, towns, how="left", predicate="within")
    joined = joined.drop(columns=["index_right"])
    joined = joined[~joined.index.duplicated()]  # 剛好壓在兩區邊界上的點只留一筆

    # 2) 沒落在任何多邊形內的（多半在海岸邊），找最近的行政區
    miss = joined["COUNTYNAME"].isna()
    if miss.any():
        fix = gpd.sjoin_nearest(
            stores.loc[miss[miss].index], towns, how="left",
            max_distance=NEAREST_MAX_METERS,
        ).drop(columns=["index_right"])
        fix = fix[~fix.index.duplicated()]
        for col in ["COUNTYNAME", "TOWNNAME", "TOWNCODE"]:
            joined.loc[fix.index, col] = fix[col]

    unmatched = joined["COUNTYNAME"].isna().sum()
    joined = joined[joined["COUNTYNAME"].notna()].copy()
    print(f"無法對應行政區（已排除）：{unmatched} 筆")

    joined["縣市"] = joined["COUNTYNAME"]
    joined["鄉鎮市區"] = joined["TOWNNAME"]
    joined["區域"] = joined["縣市"].map(COUNTY_TO_REGION).fillna("其他")

    out_cols = [
        "品牌", "店名", "區域", "縣市", "鄉鎮市區", "TOWNCODE",
        "緯度", "經度", "營業時間", "osm_type", "osm_id",
    ]
    clean = pd.DataFrame(joined[out_cols]).rename(columns={"TOWNCODE": "鄉鎮代碼"})
    clean.to_csv("stores_clean.csv", index=False, encoding="utf-8-sig")

    summary = (
        clean.groupby(["品牌", "區域", "縣市", "鄉鎮市區"])
        .size().reset_index(name="門市數")
    )
    summary.to_csv("summary_town.csv", index=False, encoding="utf-8-sig")

    print(f"\n清洗後門市：{len(clean)} 筆")
    print("\n各品牌 x 區域：")
    print(pd.crosstab(clean["品牌"], clean["區域"], margins=True, margins_name="合計"))
    print("\n門市最多的縣市（前 8）：")
    print(clean["縣市"].value_counts().head(8).to_string())
    print("\n已儲存 stores_clean.csv、summary_town.csv")


if __name__ == "__main__":
    main()