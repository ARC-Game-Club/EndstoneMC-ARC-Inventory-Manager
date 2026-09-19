# EndStone ARC Inventory / 弧光背包管理器

[![Codacy Grade](https://app.codacy.com/project/badge/Grade/4c63155069c84452b4854f597cd258a7)](https://app.codacy.com/gh/ARC-Game-Club/EndstoneMC-ARC-Inventory-Manager/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade)
[![版本](https://img.shields.io/badge/版本-0.2.1-blue.svg)](https://github.com/ARC-Game-Club/EndstoneMC-ARC-Inventory-Manager)
[![EndStone](https://img.shields.io/badge/EndStone-0.10+-green.svg)](https://github.com/EndstoneMC/endstone)

弧光系列共享背包工具插件。统一处理玩家背包的读取、匹配、扣除、发放、定点槽位、护甲与整包快照（含附魔、Lore、Bedrock NBT），供按钮商店、枪战等插件复用。

## 命名约定

| 项 | 值 |
|---|---|
| 包名 | `endstone_arc_inventory` |
| Plugin id | `arc_inventory` |
| 显示名 | 弧光背包管理器 |

本插件**无独立数据目录**（无配置文件）；纯 API 服务。

## 功能特性

- **列出背包**：槽位、类型 ID、显示名、数量、data、附魔、Lore；复杂物品可带 `nbt_b64`（潜影盒、收纳袋、附魔书、烟花等均可完整往返）；可选护甲、槽位范围
- **匹配检查**：`has_item` — 类型 / 数量 / data；有 `nbt_b64` 时按完整 NBT 比对，否则比附魔与 Lore
- **扣除物品**：`remove_item` — 默认数量不足则失败；`partial=True` 尽可能扣；返回实际数量
- **发放物品**：`give_item` / `give_item_count` — 支持定点槽、护甲槽、避开热键从末尾填充
- **快照 / 还原 / 清空**：整包含空槽；可选护甲
- **序列化**：公开 `serialize_item` / `make_item_stack`
- **NBT 内容摘要**：`summarize_item_nbt` / `api_summarize_item`——解析 `nbt_b64` 生成中文摘要行，供拍卖行、邮箱等展示富物品：容器**内容物前 4 项**（自定义名优先，其次经 `make_item_stack` + 服务器语言本地化）与**附魔前 4 条中文名**（罗马数字等级，兼容条目 `enchants` 与 NBT 根层 `ench` 两条来源），超出部分显示「等X项」

## 安装

1. 将 `endstone_arc_inventory-*.whl` 放入服务器 `plugins/`
2. 重启服务器
3. 其它插件通过 `server.get_plugin("arc_inventory")` 调用 API

### 本地构建

```bash
pip install build
python -m build
# dist/endstone_arc_inventory-<version>-py3-none-any.whl
```

## 其它插件如何调用

```python
inv = self.server.plugin_manager.get_plugin("arc_inventory")

# 列出（可选热键栏 / 护甲）
items = inv.api_get_inventory_items(player, slot_min=0, slot_max=8)
items_all = inv.api_get_inventory_items(player, include_armor=True)

item_info = {"type": "minecraft:diamond", "count": 3, "data": 0}
if inv.api_has_item(player, item_info):
    removed = inv.api_remove_item(player, item_info)  # int，布尔判断仍可用

# 定点热键 / 避开热键发放弹药
inv.api_give_item_count(player, {"type": "minecraft:iron_sword", "count": 1}, slot=0)
inv.api_give_item_count(
    player, {"type": "minecraft:arrow", "count": 64}, reserved=3, prefer_end=True
)

# 护甲
inv.api_set_armor_slot(player, "helmet", {"type": "minecraft:iron_helmet", "count": 1})

# 快照 / 还原
snap = inv.api_snapshot_inventory(player, include_armor=True)
inv.api_restore_inventory(player, snap)

inv.api_clear_inventory(player, include_contents=True, include_armor=True)
```

### `item_info` 字段

| 字段 | 必填 | 说明 |
|------|------|------|
| `type` | 是 | 物品类型 ID，如 `minecraft:diamond` |
| `count` | 是 | 数量 |
| `data` | 否 | 默认 `0` |
| `enchants` | 否 | `{附魔ID: 等级}` |
| `lore` | 否 | 字符串列表 |
| `nbt_b64` | 否 | 完整用户 NBT 的 Base64；有则匹配/发放以此为准 |

### 护甲槽名

`helmet` / `chestplate` / `leggings` / `boots` / `item_in_off_hand`

## API 一览

| 方法 | 返回 | 说明 |
|------|------|------|
| `api_get_inventory_items(player, *, include_armor=False, slot_min=None, slot_max=None)` | `list[dict]` | 背包物品列表 |
| `api_has_item(player, item_info, *, ...)` | `bool` | 是否够量匹配 |
| `api_remove_item(player, item_info, *, partial=False, ...)` | `int` | 扣除数量（布尔兼容） |
| `api_give_item(player, item_info, *, slot=None, armor_slot=None, reserved=0, prefer_end=False)` | `bool` | 是否足额发放 |
| `api_give_item_count(...)` | `int` | 实际发放数量 |
| `api_set_slot(player, slot, item_info\|None)` | `bool` | 定点写入/清空 |
| `api_set_armor_slot(player, armor_slot, item_info\|None)` | `bool` | 护甲写入/清空 |
| `api_clear_inventory(player, *, include_contents=True, include_armor=True, slot_min=None, slot_max=None)` | `bool` | 清空 |
| `api_snapshot_inventory(player, *, include_armor=True)` | `dict` | 全量快照（含空槽） |
| `api_restore_inventory(player, snapshot, *, include_armor=True)` | `bool` | 还原 |
| `api_serialize_item(stack)` | `dict\|None` | 序列化 |
| `api_make_item_stack(item_info)` | `ItemStack\|None` | 反序列化 |
| `api_summarize_item(item_info, max_entries=4)` | `list[str]` | NBT 内容物/附魔中文摘要行（拍卖、邮件展示用） |
| `api_get_inventory_manager()` | `InventoryManager\|None` | 底层管理器 |

## 与弧光系列

| 插件 | 关系 |
|------|------|
| 弧光按钮商店 / 木牌商店 | **硬依赖**本插件进行交易扣物/发物 |
| 弧光枪战 | 热键布局、护甲、赛前快照走本插件 API |
| 弧光核心 / 成就等 | 需要精确背包操作时可同样依赖本插件 |

## 更新日志

### v0.2.1
- **新增 NBT 内容摘要 API**：`api_summarize_item(item_info, max_entries=4)` / `InventoryManager.summarize_item_nbt`——
  解析富物品条目的 `nbt_b64`，生成供拍卖行、邮箱等直接展示的中文摘要行：
  - **内容物**：潜影盒等容器的 `Items` 列表，前 4 项（自定义名称优先，其次 `make_item_stack` + 服务器语言翻译的本地化名），格式 `名称×数量`，超出显示「等X项」
  - **附魔**：前 4 条中文名 + 罗马数字等级（内置基岩版 40 个附魔 id/名称键双向映射；兼容条目 `enchants` 字符串 id 与 NBT 根层 `ench` 数字 id 两条来源），超出显示「等X项」
  - 收纳袋（1.26）内容为组件化存储、不在 `nbt_b64` 中，无法摘要（调用方自行说明）
  - 纯增量改动，无任何既有接口/格式变化
- 消费方：弧光网上商城 v0.2.3（拍品详情）、弧光核心 v0.9.73（邮件附件详情）
- 合并修正（2026-09-19）：NBT 读取深度对齐基岩真实数据——`display`/`ench` 在条目根层读取，兼容 Java 风格 `tag` 包装

### v0.2.0
- **修复 NBT 序列化/还原从未生效**（感谢 [@yichen-ender](https://github.com/yichen-ender) 的 [PR #1](https://github.com/ARC-Game-Club/EndstoneMC-ARC-Inventory-Manager/pull/1)）：`endstone.nbt`（0.11.x）只导出标签类，没有 `load()` / `dump()`，原有实现两处调用均抛异常且被 `except` 静默吞掉，导致 `nbt_b64` 恒为空、完整 NBT 还原从未成功。改为遍历标签树编码 + 按记录的真实类型重建
- **修复 ByteArrayTag 导致整份 NBT 被静默丢弃**：`to_dict()` 会把 `ByteArrayTag` 变成 `bytes` 使 JSON 序列化失败，异常被吞后返回 `None` —— 潜影盒存进去但内容物丢失且无任何报错
- **修复附魔书等级变 0、烟花飞行时间变 0**：重建标签类型原本靠字段名表猜，表里没收录的字段（`lvl` / `id` / `Flight`）被降级成 `IntTag`，客户端按错误类型读取只能拿到默认值。改为编码时记录真实标签类型（`@b` / `@s` / `@i` / `@l` / `@f` / `@d` / `@B` / `@I` 标记），任何物品的任何字段都能正确往返，不再依赖猜测
- 编码失败时打 `error` 日志（含物品类型），不再静默
- 新增 `tests/test_nbt.py`（55 项，无需运行服务器）
- 兼容性：所有 `api_*` 方法签名与 `nbt_b64` 格式（对调用方仍是不透明 Base64）均未改动；旧格式裸整数的 NBT 数据仍可读。接口无需迁移

### v0.1.5
- `load_before` 增加 `arc_sign_shop`，与按钮商店一并保证启用顺序

### v0.1.4
- 扩展现有发放/列出/扣除接口：`slot` / `armor_slot` / `reserved`+`prefer_end` / `include_armor` / `slot_min`/`slot_max` / `partial`
- 新增定点槽、护甲槽、清空、整包快照/还原、公开序列化 API
- `api_remove_item` 改为返回实际扣除数量（布尔判断兼容）

### v0.1.3
确认并整理近期发放/读取相关修复，供按钮商店等插件升级对照：

- **启用顺序**：在 `on_load` 即创建 `InventoryManager`，并 `load_before` 按钮商店，避免商店先启用时拿到空管理器
- **不可堆叠数量**：发放不再硬编码 64，改为读取 `ItemStack.max_stack_size`；镐等 max=1 的物品会逐个入包，删店/购买不会少给
- **附魔书 NBT**：完整用户 NBT 还原失败时回退到附魔/Lore（`add_enchant(..., force=True)`）；也可从 NBT `ench` 列表解析附魔，避免买到无属性的附魔书

### v0.1.2
- 更早初始化 `InventoryManager`（`on_load`），并声明 `load_before` 按钮商店，避免其它插件启用时拿到空管理器

### v0.1.1
- 修复发放时硬编码堆叠 64：改为读取 `ItemStack.max_stack_size`，镐等不可堆叠物品会逐个发放，避免删店/购买数量丢失
- 修复附魔书等 NBT 还原失败后因 `if/elif` 无法回退到附魔/Lore 的问题；`add_enchant` 使用 `force=True`
- 读取附魔时不再因 `has_enchants=False` 提前放弃；可从 NBT `ench` 列表补充解析

## 许可证

见 [LICENSE](LICENSE)。
