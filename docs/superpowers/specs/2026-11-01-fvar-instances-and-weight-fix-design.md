# 设计：fvar 可变字体实例枚举 + 字重错标补偿

日期：2026-11-01
状态：已拍板（方案 A「实例=扩展面」，用户逐节确认）
来源：二轮审查拍板项 2（fvar 是否规格内）；用户字体库 1.3 万 otf 实测后拍板必做。

## 背景与问题

1. **VF 实例不可见**：fontindex 按 face 解析（TTC 多 face 已支持），可变字体
   （fvar 表）只按默认实例入一个实体——Heavy/Medium 等命名实例在库中不可见、
   搜不到、选不了。
2. **字重错标**：大量字体（名字带 Heavy 等）OS/2 usWeightClass 误标 400
   （默认值）。`rank_pool` 粗排按 `abs(e.weight - target.weight)/1000` 惩罚
   （search.py:87），正确标 900 的 VF Heavy 实例对错标静态体排位被压。
   注：rank_pool 全池迭代无截断，错标只影响排序不影响可达；但 1.3 万池下
   排位决定命中耗时与展示优先级，值得修。

## 已核验的现状事实（设计依据）

- `pairs` 缓存键 = `(fpa, fpb, text_ver)`；weight/italic 是实体属性不入键
  ——实例实体属性差异自然分化 fingerprint，缓存键零改动。
- `fingerprint_of(aliases, cmap, weight, width, italic)` 属性哈希
  （store.py:76），FP_ALGO=3。
- 字体身份去重键 = psname（无则别名并集），同键保留最高版本
  （library.py:109）——VF 各实例共享文件级 PS 名，必须加实例维度。
- `rank_pool` 全池排序无截断（search.py:130）。
- 渲染缓存无版本常量，结构不兼容弃表重建；`text_ver 经 _kv 并入 ALGO_VER`
  （cache.py:36）——递增即全量失效。
- `SCHEMA_VERSION = 1`（store.py:49，2026-11-01 绿色发布基线重置），版本不符
  → .bak 备份+全量重建（store.py:183）。
- freetype 绑定有 `Face.set_var_design_coords`，无 `set_named_instance`。
- 渲染入口链：render_bundle(path, face_index, chars) ← search.py:59/236、
  pool.py worker；render_text_strip ← jobs.py:333 预览条。
- weight 消费点仅 rank_pool 粗排 + 同名碰撞 tie-break「字重近 400」——
  均只影响排序，不进门槛建议。

## 方案（A：实例 = 扩展面）

parser 枚举 fvar 命名实例 → 每实例一行 FontRecord（新增 inst_index 维度）；
store 加列升 SCHEMA_VERSION；去重键加实例维度；FontEntity 带 var_axes 供
渲染层 set_var_design_coords；字重错标在 parser 统一改写。

否决的替代：B（实体层惰性展开——fingerprints 键表达不了实例，复杂度挪进
装载热路径）；C（不枚举，渲染时按需实例化——不满足 Heavy 面可见可选）。

## 1. parser（core/fontindex/parser.py）

### 枚举

- `parse_font_file` 每 face 判 `fvar` 表（TTC×VF 天然支持：TTCollection
  循环内逐 face 判）。
- 有 fvar → 枚举 `tt["fvar"].instances` 命名实例；无 → 现行单行。
- 行标识 `inst_index`：0 = 默认实例（静态字体同值，语义「文件默认态」），
  1..N = 命名实例声明序。

### 实例行取值

| 字段 | 口径 |
|---|---|
| weight | wght 轴坐标（轴缺失回退 OS/2 值） |
| width | **沿用 OS/2 usWidthClass**（审查 B1：wdth 注册轴是百分刻度 62.5-100，usWidthClass 是 1-9，直接入库污染整型列/粗排 /100 罚项/指纹序列化；width 罚项权重轻，CJK VF 带 wdth 轴极少，YAGNI 不折算） |
| italic | slnt 轴≠0 或 ital 轴=1 或 OS/2 fsSelection bit0 |
| cmap | 全实例共享同一 CmapRanges 对象（同文件同字形集） |
| version/psname | 文件级 ID5/ID6 沿用 |
| 别名 | 族别名（ID1/4/16 现行口径全收，保证 \fn族名 搜得到实例）+ 实例限定名：instance.subfamilyNameID 回查 name 表取子族名（含 ID2 常规回退），与族名按相同 langID 配对拼「族名 子族名」（ID16=思源黑体 + 子族 Heavy → 别名「思源黑体 Heavy」，与静态 Heavy 字体 Windows 命名习惯对齐）；子族名无该语言记录时只出英文（langID 0x409）限定名 |
| display_name | 实例行 = 限定名别名同一路径的产物（按族名显示链语言配对子族名），保证 display_name ∈ aliases 不变量（审查 B3：两路径分裂时子族名常只有英文——实测 SegUIVar 全 0x409——display 拼出 CJK 名而别名只有英文名，写回名二次检索 miss）；默认实例行沿用现行推导 |
| name_langs | **实例行置空**（审查 B2：v7 后 GUI 展示名走 name_langs 而非 display_name，实例行照收会全显示成族名无法区分；置空则 pick_display_name 走现有回退链到 display_name=限定名，GUI 零改动。代价：实例名不随界面语言切换，族名部分 CJK 优先不变） |
| name_records | 沿用文件级（命名卡以文件为单位，不逐实例展开） |

跳过规则（审查 S3 + 拍板「无名不合成」）：坐标与 fvar 默认全等的命名实例
跳过枚举（默认行已覆盖，免近重复实体）；subfamilyNameID 无法解析出子族名
的实例跳过（等价无命名实例）。

ID17 不收别名的既有原则不破：收的是「族名+子族名」拼接后的完整名（与静态
ID1="微软雅黑 Light" 同构），裸子族词不入别名。

### 字重错标补偿（static/VF 统一，行组装末段）

- 触发：**ID1/ID4/ID16 文本名**（审查 S1：aliases 含 ID6 PS 名，corbell
  实测 PS 名与族名形态漂移，不进扫描域）中**尾词**（大小写不敏感）为
  重量词 **且 weight == 400**。尾词限定对齐 Windows「族名+样式尾置」
  命名惯例，封「Black Ops One」（真 400 面，族名首词 Black）类假阳。
- 词表：Thin→100、ExtraLight/UltraLight→200、Light→300、Medium→500、
  SemiBold/DemiBold→600、Bold→700、ExtraBold/UltraBold→800、Black/Heavy→900
  （对照 OS/2 规范核验无误）。
- 仅英文词——CJK「黑体/粗」是类别语义不入表。
- VF 实例行 weight 来自轴坐标（可信）不参与补偿；默认实例行走同规则
  （族名含重量词的罕见特例，统一规则优先）。

## 2. store（core/fontindex/store.py）

- fonts 表加两列：`inst_index INTEGER NOT NULL DEFAULT 0`、
  `var_axes BLOB`（按轴声明序打包 float 坐标，**必须全长**——审查实测
  freetype set_var_design_coords 对部分长度列表静默只设首轴；NULL=静态，
  VF 默认实例行也存 NULL 走默认态渲染——渲染层直接用，不重读 fvar）；
  唯一约束 `UNIQUE(rel_path, face_index)` → 
  `UNIQUE(rel_path, face_index, inst_index)`。
- bad/files/cmap_data 表不动（同文件各实例行 cmap_id 同值，去重存储照旧）。
- fingerprints 主键 `(rel_path, face_index)` → `(rel_path, face_index,
  inst_index)`；`fingerprint_of` 算法与 FP_ALGO=3 不动（实例指纹由属性差异
  自然分化；weight 补偿走属性入哈希）。
- `SCHEMA_VERSION 1 → 2`：既有机制接管（.bak+全量重建，1.3 万字体重扫）。
- 连带记账：decisions.db 按 fingerprint 记忆——weight 被改写的存量静态体
  历史替换记忆失效一次（重选即重记，无 UI 提示）；VF 实体全新指纹无包袱。

## 3. library（core/fontindex/library.py）

- 去重身份键追加实例维度：`key = (psname 或别名并集, inst_index)`；版本
  去重逐实例成立（跨目录重复同一 VF 按实例对齐去重）。
- raw 行组装与 best 平局排序（目录序、rel_path、face_index）加 inst_index
  平局位。
- FontEntity 加字段（带默认值，存量测试构造点零改动）：
  `inst_index: int = 0`、`var_axes: tuple[float, ...] = ()`。
- `_by_alias` 别名索引机制不变：实例行族别名照收，\fn族名 命中默认+全部
  实例实体，matcher 按覆盖率/相似率自选；实例限定名归一后成独立键。
- coverage/matcher/decision/GUI 卡片零改动（全走实体属性与别名索引）。

## 4. render（core/similarity/render.py）

- `render_bundle` / `render_text_strip` 加参 `var_axes: tuple[float, ...] = ()`；
  `freetype.Face(fh, face_index)` 打开后、load_glyph 前：
  `face.set_var_design_coords(list(var_axes))`（轴序 = fvar axes 声明序 =
  parser 打包序，坐标列表须与轴数等长）。失败按现有渲染错误契约折包。
  不用 `set_var_named_instance`：FreeType 实例序（0 起）与 parser
  inst_index（0=默认态）口径不一致，且 parser 反正要读坐标算 weight
  （审查 S5 记账，免实现时再纠结）。
- 调用点同步：search.py:59/236 传 `e.var_axes`；pool.py worker 载荷加
  var_axes 字段（gather chunks 契约 docstring 同步）；jobs.py:333 预览条传
  `entity.var_axes`。
- 无跨调用污染：render_bundle 每次新开 Face；pass1/pass2 同 Face 连续渲，
  坐标持续有效即正确语义。
- 渲染/pairs 缓存键零改动；`ALGO_VER` +1 全量失效（weight 改写旧指纹的
  错键命中一并清零）。

## 5. 迁移时序

单提交落地（SCHEMA_VERSION 2 + ALGO_VER+1 + parser/library/render 全链），
不做中间态——旧库旧缓存一次性 .bak/弃表重建，无双版本共存分支。发布前
重跑 build_portable.bat（既有待办，再确认）。注意 `_quarantine` 覆盖既有
.bak（审查 N3）：本次会把 2026-11-01 基线重置留下的备份抹掉，数据可全量
重 derive，接受。

## 6. 测试口径

测试字体工厂（tests/fonts.py 扩展）：fontBuilder `setupFvar(axes, instances)`
造最小 VF fixture（wght 300-900 + Light/Black 命名实例）。gvar 不造：
parser/index 测试只需 fvar+name+cmap；render 测试口径 = 带坐标渲染不炸、
产出位图、与默认实例路径区分（轮廓级变异依赖 freetype 自身）。系统真机 VF
（Win11 SegUIVar.ttf）只作手测，不进自动化。

清单：

1. parser：VF 行数=实例数+1、inst_index 连续、实例 weight=轴值、别名含
   「族 Heavy」限定名、cmap 对象同文件共享（is 断言）、静态行不变。
2. 字重补偿：Heavy∧400→900、Bold∧400→700、Heavy∧900 不动、「黑体」不触发、
   词边界不误触。
3. store：实例行 roundtrip；同文件重扫幂等（UNIQUE 走 REPLACE）；v1 旧库
   open → .bak+全量重建。
4. library：同 psname 多实例共存不折；跨目录重复 VF 逐实例去重；
   find_by_name("族 Heavy") 命中实例、find_by_name(族名) 命中默认+实例全体。
5. render：var_axes 渲染路径；ALGO_VER+1 旧缓存全失效；pool 载荷 plumbing。
6. rank_pool：补偿后 VF@900 与错标 StaticHeavy@400 排位不再被压底。
7. e2e：含 VF 目录全流程，搜索出实例实体。

审查补位（S4）：

8. 展示/检索 roundtrip（B2/B3）：实例行 display_name ∈ aliases；写回链
   用的 display_name 再进 find_by_name 能命中；name_langs 置空后
   pick_display_name 回退到限定名。
9. 假阳样例（S1）：「Black Ops One」类族名首词重量词不改写；尾词命中
   照改。
10. var_axes 全长断言（部分长度 freetype 静默只设首轴）；坐标=默认的
    实例与子族名不可解析的实例跳过枚举。
11. instance.coordinates 缺轴键回退 axis.defaultValue（fontTools 不保证
    齐全）。

## 7. 真机验收（用户）

1.3 万库重扫耗时目测；VF 实体在库页/字体选择器展示与搜索；「族 Heavy」
可搜可选；替换预览用 VF 实例渲真实轮廓；须知：weight 被改写字体的历史
替换记忆失效一次（重选即重记）。

## 8. 已知限制（记账）

- **libass 写回侧实例不可解析**（审查实证 libass#386，用户拍板 2026-11-01
  仅记账不加徽标）：libass 不能按命名实例匹配 \fn，fontconfig provider
  显式忽略 VF 命名实例，未命中回落 fvar 默认实例（字重可能错）；GDI/
  VSFilter 栈可解析实例族名。即实例实体库内可搜可选可预览（自渲染
  set_var_design_coords 有效），mpv/libass 系播放写回可能渲成默认字重。
- 补偿假阴（S1）：camelCase PS 名（CorbelLight）无词边界不触发；非 400
  的其他错标值不覆盖（只修最普遍的「整标默认 400」形态）。
- 指纹不含 var_axes（S2）：限定名生成失败已由跳过规则堵死，残留窗口为
  「同轴坐标全等但 opsz 等其他轴不同」的两实例同指纹——与 cache.py
  「fingerprint 不含字形轮廓」既有已知限制同源，LRU 淘汰自然轮换。
- wght 轴缺失的命名实例 weight 回退 OS/2（可能被补偿规则接管）。
- instance.coordinates 缺轴键回退 axis.defaultValue（fontTools 不保证
  齐全）。
- TTC×VF 组合依赖 face 级 fvar 判定（fontTools 保证）。
- 实例行 name_langs 置空：实例名不随界面语言切换（族名部分 CJK 优先
  不变）。
- 无命名实例的 VF 只入默认实例，不合成名称（用户拍板：不同字重有不同
  名称自然是好事，没有时不合成——现有系统本就支持同族名多字重实体）。
- woff/woff2 维持现状（网页格式，libass/VSFilter 不加载，白名单静默跳过）。
