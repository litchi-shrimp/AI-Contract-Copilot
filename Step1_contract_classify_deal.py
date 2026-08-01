"""
处理合同类型分类
根据合同类型.xlsx，提取all_classify.json，放在./template_library/all_classify.json
由于生成后需要手动给每个类别起英文名作为查找索引，且目前以及处理好了。所以该文件不要运行!!否则会覆盖。
"""
import pandas as pd
import json


FILE_PATH = "./data/合同类型.xlsx"
# ========================================

# 读取 Excel
df = pd.read_excel(FILE_PATH)
df = df.iloc[:, :3]
df.columns = ["大分类", "中分类", "小分类"][:df.shape[1]]

# 只填充大分类、中分类，小分类绝不填充
df["大分类"] = df["大分类"].ffill()
if "中分类" in df.columns:
    df["中分类"] = df["中分类"].ffill()

# 安全清洗文本
def clean(val):
    if pd.isna(val):
        return ""
    s = str(val).strip()
    return s.lstrip("· ").strip()

result = {}

for _, row in df.iterrows():
    big = clean(row["大分类"])
    mid = clean(row.get("中分类", ""))
    small = clean(row.get("小分类", ""))

    if not big or big == "nan":
        continue

    # ==============================================
    # 核心逻辑
    # ==============================================

    # 大分类不存在 → 创建
    if big not in result:
        result[big] = {}

    # 情况1：只有 大分类 + 中分类，没有小分类
    if mid and not small:
        if mid not in result[big]:
            result[big][mid] = {"ID": ""}

    # 情况2：有 大分类 + 中分类 + 小分类（三级）
    elif mid and small:
        if mid not in result[big]:
            result[big][mid] = {}
        result[big][mid][small] = {"ID": ""}

# 输出 JSON
with open("./template_library/all_classify.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=4)

print("✅ 生成完成！./template_library/all_classify.json")
