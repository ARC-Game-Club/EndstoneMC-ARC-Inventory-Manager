# -*- coding: utf-8 -*-
"""NBT 序列化 / 还原单测。

不依赖运行中的服务器：用假的 ItemStack（只有一个 nbt 属性）直接驱动
InventoryManager._serialize_item_nbt / _restore_item_nbt。

验证重点：
1. 端到端往返 —— serialize -> restore 后 NBT 内容与原始一致
2. **标签类型正确** —— 全部写成 IntTag 的话服务端读回无误，但客户端渲染不出来
   （潜影盒取出来是空的）
3. 编码确定性 —— 同一份 NBT 每次编码结果必须一致，否则按 nbt_b64 精确匹配会失效
4. 边界输入不抛异常

运行： python tests/test_nbt.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from endstone.nbt import CompoundTag, ListTag, StringTag, IntTag, ByteTag, ShortTag  # noqa: E402
from endstone.nbt import (  # noqa: E402
    CompoundTag, ListTag, ByteTag, ShortTag, IntTag, LongTag,
    FloatTag, DoubleTag, StringTag, ByteArrayTag, IntArrayTag,
)
from endstone_arc_inventory.InventoryManager import (  # noqa: E402
    InventoryManager, _build_nbt, _encode_nbt_b64, _decode_nbt_b64, _tag_to_jsonable,
)

RESULTS = []


def check(name, cond):
    RESULTS.append((name, bool(cond)))


class FakePlugin:
    """InventoryManager 只用到 _safe_log 与 server。"""

    server = None

    def _safe_log(self, level, message):
        pass


class FakeStack:
    """只提供 nbt 属性的假 ItemStack。"""

    def __init__(self, tag=None):
        self._tag = tag

    @property
    def nbt(self):
        return self._tag

    @nbt.setter
    def nbt(self, value):
        self._tag = value


mgr = InventoryManager(FakePlugin())

# 真实潜影盒结构（装 barrel + dispenser，含方块状态）
SHULKER = {
    "Items": [
        {"Block": {"name": "minecraft:barrel",
                   "states": {"facing_direction": 0, "open_bit": 0},
                   "version": 18168865},
         "Count": 64, "Damage": 0, "Name": "minecraft:barrel", "Slot": 0, "WasPickedUp": 0},
        {"Block": {"name": "minecraft:dispenser",
                   "states": {"facing_direction": 3, "triggered_bit": 0},
                   "version": 18168865},
         "Count": 64, "Damage": 0, "Name": "minecraft:dispenser", "Slot": 1, "WasPickedUp": 0},
    ]
}

# ---- 1. 端到端：serialize -> restore ----
src_tag = _build_nbt(SHULKER)
b64 = mgr._serialize_item_nbt(FakeStack(src_tag))
check("含 NBT 的物品能序列化出 nbt_b64", bool(b64))

dst = FakeStack(None)
ok = mgr._restore_item_nbt(dst, b64)
check("_restore_item_nbt 返回成功", ok is True)
check("还原后 nbt 非空", dst.nbt is not None)
check("还原后内容与原始完全一致", dst.nbt.to_dict() == SHULKER)

# 还原后再序列化，b64 应完全相同（匹配逻辑依赖这一点）
check("还原后再次序列化得到同一个 b64", mgr._serialize_item_nbt(dst) == b64)

# ---- 2. 标签类型必须正确（客户端能否渲染的关键）----
items = dst.nbt["Items"]
check("Items 是列表且长度正确", items.size() == 2)
it0 = items[0]
check("Slot 必须是 ByteTag", type(it0["Slot"]).__name__ == "ByteTag")
check("Count 必须是 ByteTag", type(it0["Count"]).__name__ == "ByteTag")
check("Damage 必须是 ShortTag", type(it0["Damage"]).__name__ == "ShortTag")
check("Name 必须是 StringTag", type(it0["Name"]).__name__ == "StringTag")
check("WasPickedUp 必须是 ByteTag", type(it0["WasPickedUp"]).__name__ == "ByteTag")

blk = it0["Block"]
check("Block 必须是 CompoundTag", type(blk).__name__ == "CompoundTag")
check("Block.version 必须是 IntTag", type(blk["version"]).__name__ == "IntTag")
states = blk["states"]
check("states 必须是 CompoundTag（嵌套下钻）", type(states).__name__ == "CompoundTag")
check("facing_direction 必须是 IntTag", type(states["facing_direction"]).__name__ == "IntTag")
check("方块位字段 open_bit 必须是 ByteTag", type(states["open_bit"]).__name__ == "ByteTag")
check("方块位字段 triggered_bit 必须是 ByteTag",
      type(items[1]["Block"]["states"]["triggered_bit"]).__name__ == "ByteTag")

# ---- 3. 编码确定性 ----
check("同一份 NBT 编码结果稳定", _encode_nbt_b64(SHULKER) == _encode_nbt_b64(SHULKER))


def _reverse_tag(tag):
    """把复合标签的键序倒过来，用于验证编码与键序无关。"""
    cls = type(tag).__name__
    if cls == "CompoundTag":
        out = CompoundTag()
        for k, v in reversed(list(tag.items())):
            out[k] = _reverse_tag(v)
        return out
    if cls == "ListTag":
        out = ListTag()
        for v in tag:
            out.append(_reverse_tag(v))
        return out
    return tag


check("键序不同但内容相同 → 编码一致",
      _encode_nbt_b64(_tag_to_jsonable(_reverse_tag(src_tag))) == b64)

# ---- 4. 不同内容必须区分开 ----
OTHER = {"Items": [{"Name": "minecraft:stone", "Count": 1, "Slot": 0}]}
check("内容不同 → 编码不同", _encode_nbt_b64(OTHER) != b64)

# ---- 5. 边界输入 ----
check("空 dict 编码为 None", _encode_nbt_b64({}) is None)
check("None 编码为 None", _encode_nbt_b64(None) is None)
check("空串解码为 None", _decode_nbt_b64("") is None)
check("垃圾输入解码为 None（不抛异常）", _decode_nbt_b64("!!!not-base64!!!") is None)
check("无 NBT 的物品序列化为 None", mgr._serialize_item_nbt(FakeStack(None)) is None)
check("空 CompoundTag 序列化为 None", mgr._serialize_item_nbt(FakeStack(CompoundTag())) is None)
check("还原垃圾 b64 返回 False", mgr._restore_item_nbt(FakeStack(None), "!!!bad!!!") is False)

# ---- 6. 类型映射表覆盖常见方块状态 ----
BOOL_LIKE = {"open_bit": 1, "triggered_bit": 0, "powered_bit": 1, "waterlogged": 0,
             "door_hinge_bit": 1, "upside_down_bit": 0}
t = _build_nbt(BOOL_LIKE)
check("常见方块位字段全部还原为 ByteTag",
      all(type(t[k]).__name__ == "ByteTag" for k in BOOL_LIKE))
int_like = _build_nbt({"facing_direction": 3, "version": 18168865, "direction": 1})
check("facing_direction/version/direction 为 IntTag",
      all(type(int_like[k]).__name__ == "IntTag" for k in int_like))

# ---- 7. to_dict() 会丢类型的四种标签（必须靠 @ 标记保留）----
# 这些类型直接用 to_dict() 的话：ByteArrayTag -> bytes（json 崩，NBT 被静默丢弃）、
# IntArrayTag -> list、FloatTag -> float、LongTag -> int（重建后类型就错了）
ALL_TYPES = CompoundTag()
ALL_TYPES["Slot"] = ByteTag(3)
ALL_TYPES["Damage"] = ShortTag(7)
ALL_TYPES["version"] = IntTag(18168865)
ALL_TYPES["big"] = LongTag(2 ** 40)
ALL_TYPES["ratio"] = FloatTag(1.5)
ALL_TYPES["precise"] = DoubleTag(3.141592653589793)
ALL_TYPES["Name"] = StringTag("minecraft:shulker_box")
ALL_TYPES["raw"] = ByteArrayTag(b"\x01\x02\xff")
ALL_TYPES["arr"] = IntArrayTag([10, 20, 30])

encoded = _tag_to_jsonable(ALL_TYPES)
try:
    import json
    json.dumps(encoded, ensure_ascii=False, sort_keys=True)
    check("含 ByteArrayTag 的 NBT 可 JSON 序列化（原来会崩/被丢弃）", True)
except TypeError as e:
    check("含 ByteArrayTag 的 NBT 可 JSON 序列化（原来会崩/被丢弃）", False)
    print("   错误:", e)

rt = _build_nbt(encoded)
check("ByteArrayTag 还原为 ByteArrayTag", type(rt["raw"]).__name__ == "ByteArrayTag")
check("  ByteArrayTag 字节完整", bytes(rt["raw"]) == b"\x01\x02\xff")
check("IntArrayTag 还原为 IntArrayTag（不再降级成 ListTag）",
      type(rt["arr"]).__name__ == "IntArrayTag" and list(rt["arr"]) == [10, 20, 30])
check("FloatTag 还原为 FloatTag（不再降级成 DoubleTag）", type(rt["ratio"]).__name__ == "FloatTag")
check("LongTag 还原为 LongTag（不再降级成 IntTag）", type(rt["big"]).__name__ == "LongTag")
check("  LongTag 值未丢精度", rt["big"].value == 2 ** 40)
check("DoubleTag 仍是 DoubleTag", type(rt["precise"]).__name__ == "DoubleTag")
check("往返后内容完全一致", rt.to_dict() == ALL_TYPES.to_dict())
check("字符串保持裸值（JSON 仍可读）", encoded["Name"] == "minecraft:shulker_box")
check("数值类型都带 @ 标记",
      encoded["version"] == {"@i": 18168865} and encoded["Slot"] == {"@b": 3})
check("数组类型标记正确",
      encoded["raw"] == {"@B": "AQL/"} and encoded["arr"] == {"@I": [10, 20, 30]})

# 用假 ItemStack 走完整方法链，确认不会再静默丢 NBT
_box = FakeStack(ALL_TYPES)
_b64 = mgr._serialize_item_nbt(_box)
check("含 ByteArrayTag 的物品能序列化出 nbt_b64（原来是 None）", bool(_b64))
_dst = FakeStack(None)
check("能还原回物品", mgr._restore_item_nbt(_dst, _b64) is True)
check("  还原后 ByteArrayTag 仍是 ByteArrayTag", type(_dst.nbt["raw"]).__name__ == "ByteArrayTag")

# ---- 8. 旧格式（无 @ 标记）向后兼容 ----
LEGACY = {"Items": [{"Count": 64, "Slot": 0, "Name": "minecraft:barrel",
                     "Damage": 0, "WasPickedUp": 0}]}
_lb = _build_nbt(LEGACY)
check("旧格式（无标记）仍能正确重建", _lb.to_dict() == LEGACY)
check("  旧格式 Slot 仍是 ByteTag", type(_lb["Items"][0]["Slot"]).__name__ == "ByteTag")

# ---- 9. 数值类型必须显式记录，不能靠字段名猜 ----
# 回归背景：曾经用字段名表猜类型，表里没收录的字段一律降级成 IntTag。
# 结果附魔书的 lvl/id（Short）与烟花火箭的 Flight（Byte）都被写成 IntTag，
# 客户端按错误类型读 → 附魔等级显示 0、烟花飞行时间显示 0。
# 现在 _tag_to_jsonable 直接记录真实类型，重建不再需要猜测。

# 附魔书：ench 是 [{id: Short, lvl: Short}]，两个字段名都不在旧表里
EBOOK = CompoundTag()
_el = ListTag()
for _eid, _lvl in ((9, 5), (17, 3)):
    _e = CompoundTag()
    _e["id"] = ShortTag(_eid)
    _e["lvl"] = ShortTag(_lvl)
    _el.append(_e)
EBOOK["ench"] = _el
EBOOK["RepairCost"] = IntTag(2)
_eb = _build_nbt(json.loads(json.dumps(_tag_to_jsonable(EBOOK))))
check("附魔书往返内容一致", _eb.to_dict() == EBOOK.to_dict())
check("ench[].lvl 是 ShortTag（原来被写成 IntTag → 等级显示 0）",
      type(_eb["ench"][0]["lvl"]).__name__ == "ShortTag")
check("  lvl 值正确 = 5", _eb["ench"][0]["lvl"].value == 5)
check("ench[].id 是 ShortTag", type(_eb["ench"][0]["id"]).__name__ == "ShortTag")
check("RepairCost 仍是 IntTag", type(_eb["RepairCost"]).__name__ == "IntTag")

# 烟花火箭：Fireworks.Flight 是 Byte，Explosions[].Type 是 Byte
FIREWORK = CompoundTag()
FIREWORK["Fireworks"] = CompoundTag()
FIREWORK["Fireworks"]["Flight"] = ByteTag(3)
_fx = CompoundTag()
_fx["Type"] = ByteTag(1)
_fx["Colors"] = IntArrayTag([11743532])
FIREWORK["Fireworks"]["Explosions"] = ListTag()
FIREWORK["Fireworks"]["Explosions"].append(_fx)
_fw = _build_nbt(json.loads(json.dumps(_tag_to_jsonable(FIREWORK))))
check("烟花往返内容一致", _fw.to_dict() == FIREWORK.to_dict())
check("Fireworks.Flight 是 ByteTag（原来被写成 IntTag → 飞行时间显示 0）",
      type(_fw["Fireworks"]["Flight"]).__name__ == "ByteTag")
check("  Flight 值正确 = 3", _fw["Fireworks"]["Flight"].value == 3)
check("嵌套 Explosions[].Type 是 ByteTag",
      type(_fw["Fireworks"]["Explosions"][0]["Type"]).__name__ == "ByteTag")

# ---- 结果输出 ----
fails = [n for n, ok in RESULTS if not ok]
print("PASS: %d / %d" % (len(RESULTS) - len(fails), len(RESULTS)))
for n, ok in RESULTS:
    print(("[OK]   " if ok else "[FAIL] ") + n)
if fails:
    print("FAILED:", fails)
sys.exit(1 if fails else 0)
