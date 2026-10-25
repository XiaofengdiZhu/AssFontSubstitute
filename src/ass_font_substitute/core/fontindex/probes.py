from ass_font_substitute.core.models import CmapRanges

# 简/繁探针：≥60 对「同字简繁对」。每对 SC 与 TC 异码，两集合零交集、去重后等长。
PROBE_PAIRS: tuple[tuple[str, str], ...] = (
    ("爱", "愛"), ("备", "備"), ("笔", "筆"), ("币", "幣"), ("边", "邊"),
    ("变", "變"), ("标", "標"), ("别", "別"), ("宾", "賓"), ("补", "補"),
    ("参", "參"), ("惨", "慘"), ("灿", "燦"), ("苍", "蒼"), ("层", "層"),
    ("尝", "嘗"), ("长", "長"), ("偿", "償"), ("厂", "廠"), ("车", "車"),
    ("齿", "齒"), ("迟", "遲"), ("冲", "衝"), ("丑", "醜"), ("础", "礎"),
    ("处", "處"), ("触", "觸"), ("传", "傳"), ("闯", "闖"), ("创", "創"),
    ("纯", "純"), ("词", "詞"), ("辞", "辭"), ("从", "從"), ("丛", "叢"),
    ("凑", "湊"), ("窜", "竄"), ("错", "錯"), ("达", "達"), ("带", "帶"),
    ("单", "單"), ("担", "擔"), ("胆", "膽"), ("弹", "彈"), ("当", "當"),
    ("党", "黨"), ("东", "東"), ("动", "動"), ("断", "斷"), ("队", "隊"),
    ("发", "發"), ("飞", "飛"), ("废", "廢"), ("费", "費"), ("丰", "豐"),
    ("风", "風"), ("凤", "鳳"), ("复", "復"), ("盖", "蓋"), ("刚", "剛"),
    ("广", "廣"), ("归", "歸"), ("龟", "龜"), ("国", "國"),
)
PROBE_SC: frozenset[str] = frozenset(sc for sc, _ in PROBE_PAIRS)
PROBE_TC: frozenset[str] = frozenset(tc for _, tc in PROBE_PAIRS)

# 注意：「仿」须先于「宋」判定，否则「仿宋」会被判成 宋。
CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    ("仿", "仿"),
    ("黑", "黑"),
    ("楷", "楷"),
    ("宋", "宋"),
    ("明", "宋"),
    ("圆", "圆"),
    ("圓", "圆"),
]

# 无中文名的字体（如微软雅黑 UI 的 zh-CN 名就是英文「Microsoft YaHei UI」）
# 靠罗马化/英文名兜底；token 取品牌专名，casefold 后子串匹配，避免误伤。
# 匹配序：每个别名先扫 CJK 组再扫 EN 组（别名内 CJK 命中优先）；EN 组在
# 别名序上位于靠前别名时也会先命中——EN token 均为品牌专名，实测无误配。
EN_CATEGORY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("fangsong", "仿"), ("simfang", "仿"),
    ("yahei", "黑"), ("simhei", "黑"), ("heiti", "黑"),
    ("source han sans", "黑"), ("noto sans cjk", "黑"),
    ("noto sans sc", "黑"), ("noto sans tc", "黑"), ("noto sans hk", "黑"),
    ("ms gothic", "黑"), ("yu gothic", "黑"), ("meiryo", "黑"),
    ("simsun", "宋"), ("nsimsun", "宋"), ("songti", "宋"),
    ("source han serif", "宋"), ("noto serif cjk", "宋"),
    ("noto serif sc", "宋"), ("noto serif tc", "宋"),
    ("ms mincho", "宋"), ("yu mincho", "宋"),
    ("kaiti", "楷"), ("simkai", "楷"),
    ("yuanti", "圆"),
)
THRESHOLD = 0.90


def classify_script(cmap: CmapRanges) -> str:
    hs, ht = cmap.hit_ratio(PROBE_SC), cmap.hit_ratio(PROBE_TC)
    sc, tc = hs >= THRESHOLD, ht >= THRESHOLD
    if sc and tc:
        return "简繁"
    if sc:
        return "简"
    if tc:
        return "繁"
    return "未标注"


def classify_category(aliases: list[str]) -> str:
    for alias in aliases:
        folded = alias.casefold()
        for kw, cat in CATEGORY_KEYWORDS:
            if kw in alias:
                return cat
        for kw, cat in EN_CATEGORY_KEYWORDS:
            if kw in folded:
                return cat
    return "未分类"
