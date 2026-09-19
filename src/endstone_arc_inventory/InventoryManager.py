# -*- coding: utf-8 -*-
"""
背包管理类：统一负责玩家背包的读取、匹配、移除与发放。
复用附魔/洛尔等 Endstone API 的转换与比较逻辑，便于维护与扩展。
Endstone ItemMeta.enchants 返回 dict[Enchantment, int]，键不可哈希会报错，
故通过 get_enchant_level(id: str) 逐个查询已知附魔 id 获取等级。
"""
import base64
import json
import traceback
from typing import Any, Dict, List, Optional, Tuple, Union

# Endstone 已知附魔 id 列表（minecraft:xxx），用于 get_enchant_level 逐个查询，避免访问 .enchants
_ENCHANT_IDS: List[str] = []

# 护甲 / 副手属性名（PlayerInventory）
ARMOR_ATTRS: Tuple[str, ...] = (
    "helmet",
    "chestplate",
    "leggings",
    "boots",
    "item_in_off_hand",
)

# ---------------------------------------------------------------------------
# NBT 序列化 / 还原
#
# endstone 0.11.3 的 endstone.nbt 只导出标签类，**没有 load() / dump()**，
# 因此无法走二进制往返，只能遍历标签树 -> 重建。
# 重建时必须按字段名还原成正确的标签类型：全部写成 IntTag 的话服务端读回无误，
# 但客户端渲染不出来（潜影盒取出来是空的）。
# 字段表来自基岩版真实 NBT 数据实测。
# ---------------------------------------------------------------------------

# 标签类型为 Byte 的字段（含 Block.states 里的方块状态位字段）
_NBT_BYTE_FIELDS = {
    "Slot", "Count", "WasPickedUp", "inverted", "Findable",
    "KeepPacked", "OnGround", "Fire", "SpawnEgg",
    "open_bit", "triggered_bit", "powered_bit", "toggle_bit",
    "occupied_bit", "in_wall_bit", "button_pressed_bit",
    "top_slot_bit", "conditional_bit", "update_bit",
    "waterlogged", "stripped_bit", "extinguished",
    "drag_down", "paused", "attached_bit", "disarmed_bit",
    "door_hinge_bit", "upper_block_bit", "upside_down_bit",
    "infiniburn_bit", "allow_underwater_bit",
    "brewing_stand_slot_a_bit", "brewing_stand_slot_b_bit",
    "brewing_stand_slot_c_bit", "covered_bit",
}

# 标签类型为 Short 的字段
_NBT_SHORT_FIELDS = {"Damage", "Health", "Age"}


def _tag_to_jsonable(tag: Any) -> Any:
    """把 NBT 标签树转成可 JSON 序列化的结构，**每个数值都带上真实标签类型**。

    两条都不能省：

    1) 不能用 CompoundTag.to_dict() —— 它是有损的：
       ByteArrayTag -> bytes（json.dumps 抛 TypeError，被 except 吞掉后返回 None，
       结果是**静默丢失整份 NBT** —— 潜影盒存进去但内容物没了且无任何报错）、
       IntArrayTag -> list、FloatTag -> float、LongTag -> int。

    2) **不能靠字段名猜类型**。曾经用 _NBT_BYTE_FIELDS / _NBT_SHORT_FIELDS 两张
       表，表里没收录的字段一律降级成 IntTag。结果附魔书的 `lvl`/`id`（应为 Short）
       和烟花火箭的 `Flight`（应为 Byte）都被写成了 IntTag，客户端按错误类型读，
       表现为**附魔等级变 0、烟花飞行时间变 0**。这种"漏一个字段就坏一种物品"的
       做法不可持续，所以现在改为编码时把真实类型记下来，重建时不需要任何猜测。

    表示法：数值 -> {"@b"/"@s"/"@i"/"@l"/"@f"/"@d": 值}
            字符串、列表、复合标签保持自然形态。
    标记键以 @ 开头 —— 基岩版 NBT 字段名不会以 @ 开头，不会冲突。
    """
    if tag is None:
        return None
    cls = type(tag).__name__
    if cls == "CompoundTag":
        out: Dict[str, Any] = {}
        for k, v in tag.items():
            out[str(k)] = _tag_to_jsonable(v)
        return out
    if cls == "ListTag":
        return [_tag_to_jsonable(v) for v in tag]
    if cls == "ByteArrayTag":
        return {"@B": base64.b64encode(bytes(tag)).decode("ascii")}
    if cls == "IntArrayTag":
        return {"@I": [int(x) for x in tag]}
    if cls == "ByteTag":
        return {"@b": int(tag.value)}
    if cls == "ShortTag":
        return {"@s": int(tag.value)}
    if cls == "IntTag":
        return {"@i": int(tag.value)}
    if cls == "LongTag":
        return {"@l": int(tag.value)}
    if cls == "FloatTag":
        return {"@f": float(tag.value)}
    if cls == "DoubleTag":
        return {"@d": float(tag.value)}
    if cls == "StringTag":
        return str(tag.value)
    # 未知类型：退回 to_dict()，至少不抛异常
    try:
        return tag.to_dict()
    except Exception:
        return None


def _build_nbt(value: Any, field_name: str = "") -> Any:
    """把普通 Python 值按基岩版正确的标签类型重建为 NBT 标签树。

    数值类型优先看 @ 标记（由 _tag_to_jsonable 写入，是标签的真实类型）。
    只有**旧格式**的裸整数才回退到按字段名猜 —— 那是本次改动之前存的数据，
    新写入的数据一律带标记，不再依赖字段名表。
    """
    from endstone.nbt import (CompoundTag, ListTag, StringTag, IntTag, LongTag,
                             ByteTag, ShortTag, DoubleTag, FloatTag,
                             ByteArrayTag, IntArrayTag)
    if isinstance(value, dict):
        # 单键 @ 标记 → 显式类型的标签（见 _tag_to_jsonable）
        if len(value) == 1:
            mk, mv = next(iter(value.items()))
            if mk == "@b":
                return ByteTag(int(mv))
            if mk == "@s":
                return ShortTag(int(mv))
            if mk == "@i":
                return IntTag(int(mv))
            if mk == "@l":
                return LongTag(int(mv))
            if mk == "@f":
                return FloatTag(float(mv))
            if mk == "@d":
                return DoubleTag(float(mv))
            if mk == "@B":
                return ByteArrayTag(base64.b64decode(mv))
            if mk == "@I":
                return IntArrayTag([int(x) for x in mv])
        tag = CompoundTag()
        for k, v in value.items():
            tag[str(k)] = _build_nbt(v, str(k))
        return tag
    if isinstance(value, (list, tuple)):
        lst = ListTag()
        for elem in value:
            lst.append(_build_nbt(elem, field_name))
        return lst
    if isinstance(value, bool):
        return ByteTag(1 if value else 0)
    if isinstance(value, int):
        # 旧格式兼容：没有 @ 标记的裸整数只能按字段名猜
        if field_name in _NBT_BYTE_FIELDS:
            return ByteTag(value)
        if field_name in _NBT_SHORT_FIELDS:
            return ShortTag(value)
        return IntTag(value)
    if isinstance(value, float):
        return DoubleTag(value)
    return StringTag(str(value))


def _encode_nbt_b64(nbt_dict: Optional[dict]) -> Optional[str]:
    """dict -> base64(JSON)。sort_keys 保证同一份 NBT 每次编码结果一致，匹配/比对才可靠。"""
    if not nbt_dict:
        return None
    try:
        raw = json.dumps(nbt_dict, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
        return base64.b64encode(raw).decode("ascii")
    except Exception:
        return None


def _decode_nbt_b64(nbt_b64: str) -> Any:
    """base64(JSON) -> NBT 标签树。失败返回 None。"""
    if not nbt_b64:
        return None
    try:
        payload = json.loads(base64.b64decode(nbt_b64).decode("utf-8"))
        if not payload:
            return None
        return _build_nbt(payload)
    except Exception:
        return None


def _normalize_enchant_id(eid: str) -> str:
    """统一为 minecraft:xxx 格式，兼容旧数据中的短 id。"""
    if not eid:
        return eid
    if eid.startswith("minecraft:"):
        return eid
    return "minecraft:" + eid.replace(" ", "_").lower()


def _build_enchant_ids() -> List[str]:
    """从 endstone.enchantments.Enchantment 收集所有附魔字符串 id（仅执行一次）。"""
    global _ENCHANT_IDS
    if _ENCHANT_IDS:
        return _ENCHANT_IDS
    try:
        from endstone.enchantments import Enchantment
        for name in dir(Enchantment):
            if name.isupper():
                val = getattr(Enchantment, name, None)
                if isinstance(val, str) and val.startswith("minecraft:"):
                    _ENCHANT_IDS.append(val)
    except Exception:
        pass
    if not _ENCHANT_IDS:
        _ENCHANT_IDS = [
            "minecraft:aqua_affinity", "minecraft:bane_of_arthropods",
            "minecraft:blast_protection", "minecraft:breach", "minecraft:channeling",
            "minecraft:binding", "minecraft:vanishing", "minecraft:density",
            "minecraft:depth_strider", "minecraft:efficiency", "minecraft:feather_falling",
            "minecraft:fire_aspect", "minecraft:fire_protection", "minecraft:flame",
            "minecraft:frost_walker", "minecraft:impaling", "minecraft:infinity",
            "minecraft:knockback", "minecraft:looting", "minecraft:loyalty",
            "minecraft:luck_of_the_sea", "minecraft:lure", "minecraft:mending",
            "minecraft:multishot", "minecraft:piercing", "minecraft:power",
            "minecraft:projectile_protection", "minecraft:protection", "minecraft:punch",
            "minecraft:quick_charge", "minecraft:respiration", "minecraft:riptide",
            "minecraft:sharpness", "minecraft:silk_touch", "minecraft:smite",
            "minecraft:soul_speed", "minecraft:swift_sneak", "minecraft:thorns",
            "minecraft:unbreaking", "minecraft:wind_burst",
        ]
    return _ENCHANT_IDS


class InventoryManager:
    """
    专门负责玩家背包物品管理的类。
    依赖插件实例以使用 _safe_log 与 server（如语言翻译）。
    """

    def __init__(self, plugin: Any):
        """
        :param plugin: 插件实例，需提供 _safe_log(level, message) 与 server
        """
        self._plugin = plugin
        self._server = getattr(plugin, "server", None)

    def _log(self, level: str, message: str) -> None:
        if hasattr(self._plugin, "_safe_log") and self._plugin._safe_log:
            self._plugin._safe_log(level, message)
        else:
            print(f"[{level.upper()}] {message}")

    def _serialize_item_nbt(self, item_stack: Any) -> Optional[str]:
        """
        将物品完整 NBT 序列化为 Base64（内容为 UTF-8 JSON），用于完整还原潜影盒/收纳袋内容、
        附魔书、铁砧命名等 ItemMeta 无法表达的标签。

        注意：endstone 0.11.3 的 CompoundTag 没有 dump()，无法做二进制序列化，
        因此走标签树遍历（_tag_to_jsonable）+ _build_nbt() 重建的路子；
        编码用 sort_keys 保证结果稳定。

        这里不能直接用 CompoundTag.to_dict()：它会把 ByteArrayTag 变成 bytes
        导致 JSON 序列化失败，进而**静默丢掉整份 NBT**。
        """
        def _type_id() -> str:
            t = getattr(item_stack, "type", None)
            return str(getattr(t, "id", t) or "?")

        try:
            if not item_stack:
                return None
            nbt_compound = getattr(item_stack, "nbt", None)
            if nbt_compound is None:
                return None
            keys_fn = getattr(nbt_compound, "keys", None)
            if callable(keys_fn) and not list(keys_fn()):
                return None
            data = _tag_to_jsonable(nbt_compound)
            if not data:
                return None
            encoded = _encode_nbt_b64(data)
            if encoded is None:
                # 编码失败绝不能静默：那会变成"物品存进去了但内容没了"，
                # 而调用方完全看不出来。这里明确报出来。
                self._log(
                    "error",
                    f"[ARCInventory] NBT 编码失败，该物品的内容将被丢弃: type={_type_id()}",
                )
            return encoded
        except Exception as e:
            self._log(
                "error",
                f"[ARCInventory] NBT 序列化异常，该物品的内容将被丢弃: "
                f"type={_type_id()} err={e}",
            )
            return None

    def _get_item_enchants(self, item_stack: Any) -> Dict[str, int]:
        """
        从 ItemStack 安全读取附魔信息（str->int）。
        不访问 ItemMeta.enchants（会触发 unhashable），改用 get_enchant_level(id) 逐个查询。
        附魔书等物品可能 has_enchants=False 但仍能按 id 读到等级，故不提前因 has_enchants 返回空。
        """
        result: Dict[str, int] = {}
        if not item_stack:
            return result
        meta = getattr(item_stack, "item_meta", None)
        get_level = getattr(meta, "get_enchant_level", None) if meta is not None else None
        if callable(get_level):
            try:
                for enchant_id in _build_enchant_ids():
                    try:
                        level = get_level(enchant_id)
                        if level and int(level) > 0:
                            result[enchant_id] = int(level)
                    except Exception:
                        continue
            except Exception as enc_e:
                self._log(
                    "warning",
                    f"[ARCInventory] Get enchants (get_enchant_level) failed: {enc_e}\n{traceback.format_exc()}",
                )
        if not result:
            result = self._get_enchants_from_nbt(item_stack)
        return result

    def _get_enchants_from_nbt(self, item_stack: Any) -> Dict[str, int]:
        """从用户 NBT 的 ench 列表尽量解析附魔（Bedrock 附魔书主要靠此）。"""
        result: Dict[str, int] = {}
        try:
            nbt = getattr(item_stack, "nbt", None)
            if nbt is None:
                return result
            ench = None
            if hasattr(nbt, "get"):
                try:
                    ench = nbt.get("ench")
                except Exception:
                    ench = None
            if ench is None:
                try:
                    ench = nbt["ench"]
                except Exception:
                    return result
            if ench is None:
                return result
            # ListTag / list
            try:
                entries = list(ench)
            except Exception:
                return result
            id_by_num = {
                # Bedrock legacy numeric ids commonly seen on enchanted books
                0: "minecraft:protection",
                1: "minecraft:fire_protection",
                2: "minecraft:feather_falling",
                3: "minecraft:blast_protection",
                4: "minecraft:projectile_protection",
                5: "minecraft:thorns",
                6: "minecraft:respiration",
                7: "minecraft:depth_strider",
                8: "minecraft:aqua_affinity",
                9: "minecraft:sharpness",
                10: "minecraft:smite",
                11: "minecraft:bane_of_arthropods",
                12: "minecraft:knockback",
                13: "minecraft:fire_aspect",
                14: "minecraft:looting",
                15: "minecraft:efficiency",
                16: "minecraft:silk_touch",
                17: "minecraft:unbreaking",
                18: "minecraft:fortune",
                19: "minecraft:power",
                20: "minecraft:punch",
                21: "minecraft:flame",
                22: "minecraft:infinity",
                23: "minecraft:luck_of_the_sea",
                24: "minecraft:lure",
                25: "minecraft:frost_walker",
                26: "minecraft:mending",
                27: "minecraft:binding",
                28: "minecraft:vanishing",
                29: "minecraft:impaling",
                30: "minecraft:riptide",
                31: "minecraft:loyalty",
                32: "minecraft:channeling",
                33: "minecraft:multishot",
                34: "minecraft:piercing",
                35: "minecraft:quick_charge",
                36: "minecraft:soul_speed",
                37: "minecraft:swift_sneak",
            }
            for entry in entries:
                try:
                    eid_raw = None
                    lvl_raw = None
                    if hasattr(entry, "get"):
                        eid_raw = entry.get("id")
                        lvl_raw = entry.get("lvl")
                    if eid_raw is None:
                        try:
                            eid_raw = entry["id"]
                        except Exception:
                            continue
                    if lvl_raw is None:
                        try:
                            lvl_raw = entry["lvl"]
                        except Exception:
                            continue
                    # IntTag / ShortTag 等可能包一层 .value
                    if hasattr(eid_raw, "value"):
                        eid_raw = eid_raw.value
                    if hasattr(lvl_raw, "value"):
                        lvl_raw = lvl_raw.value
                    level = int(lvl_raw)
                    if level <= 0:
                        continue
                    if isinstance(eid_raw, str):
                        eid = _normalize_enchant_id(eid_raw)
                    else:
                        eid = id_by_num.get(int(eid_raw))
                    if eid:
                        result[eid] = level
                except Exception:
                    continue
        except Exception:
            return {}
        return result

    def _get_item_lore(self, item_stack: Any) -> List[str]:
        """从 ItemStack 安全读取 Lore。"""
        if not item_stack or not getattr(item_stack, "item_meta", None):
            return []
        if not getattr(item_stack.item_meta, "has_lore", False):
            return []
        try:
            lore = item_stack.item_meta.lore
            return list(lore) if isinstance(lore, list) else []
        except Exception:
            return []

    def _item_stack_matches_info(
        self,
        item_stack: Any,
        required_type: str,
        required_data: int,
        required_enchants: Dict[str, int],
        required_lore: List[str],
        required_nbt_b64: Optional[str] = None,
    ) -> bool:
        """判断单个 ItemStack 是否与 item_info 要求一致（类型、data；若有 nbt_b64 则比对完整 NBT，否则比对附魔与 Lore）。"""
        if not item_stack or not item_stack.type:
            return False
        if item_stack.type.id != required_type or item_stack.data != required_data:
            return False
        if required_nbt_b64:
            serialized = self._serialize_item_nbt(item_stack)
            return serialized is not None and serialized == required_nbt_b64
        item_enchants = self._get_item_enchants(item_stack)
        item_lore = self._get_item_lore(item_stack)
        if required_enchants:
            for eid, level in required_enchants.items():
                key = eid if eid in item_enchants else _normalize_enchant_id(eid)
                if item_enchants.get(key) != level:
                    return False
        if required_lore:
            if len(required_lore) != len(item_lore):
                return False
            for i, line in enumerate(required_lore):
                if i >= len(item_lore) or item_lore[i] != line:
                    return False
        return True

    def _slot_in_range(
        self,
        slot_index: int,
        slot_min: Optional[int],
        slot_max: Optional[int],
    ) -> bool:
        if slot_min is not None and slot_index < int(slot_min):
            return False
        if slot_max is not None and slot_index > int(slot_max):
            return False
        return True

    def _build_item_entry(
        self,
        player: Any,
        item_stack: Any,
        *,
        slot_index: Optional[Union[int, str]] = None,
        armor_slot: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not item_stack or not getattr(item_stack, "type", None):
            return None
        if int(getattr(item_stack, "amount", 0) or 0) <= 0:
            return None
        try:
            item_type_id = item_stack.type.id
            item_type_translation_key = item_stack.type.translation_key
            display_name = item_type_id
            if self._server and hasattr(self._server, "language"):
                try:
                    display_name = self._server.language.translate(
                        item_type_translation_key,
                        None,
                        getattr(player, "locale", None),
                    )
                except Exception:
                    pass
            if item_stack.item_meta and getattr(
                item_stack.item_meta, "has_display_name", False
            ):
                display_name = item_stack.item_meta.display_name
            enchants = self._get_item_enchants(item_stack)
            lore = self._get_item_lore(item_stack)
            nbt_b64 = self._serialize_item_nbt(item_stack)
            entry: Dict[str, Any] = {
                "type": item_type_id,
                "type_translation_key": item_type_translation_key,
                "name": display_name,
                "count": item_stack.amount,
                "data": item_stack.data,
                "enchants": enchants,
                "lore": lore,
            }
            if slot_index is not None:
                entry["slot_index"] = slot_index
            if armor_slot:
                entry["armor_slot"] = armor_slot
            if nbt_b64:
                entry["nbt_b64"] = nbt_b64
            return entry
        except Exception as item_e:
            self._log(
                "warning",
                f"[ARCInventory] item entry build failed: {item_e}\n{traceback.format_exc()}",
            )
            return None

    def get_inventory_items(
        self,
        player: Any,
        *,
        include_armor: bool = False,
        slot_min: Optional[int] = None,
        slot_max: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取玩家背包中所有有效物品的列表。
        每项为 dict：type, type_translation_key, name, count, data, enchants, lore, slot_index；
        若物品含完整用户 NBT（如附魔书），另含 nbt_b64（Base64 二进制 NBT）。
        include_armor=True 时追加护甲/副手，带 armor_slot 字段。
        slot_min/slot_max 仅过滤主背包槽位（含端点）。
        """
        items: List[Dict[str, Any]] = []
        try:
            inventory = player.inventory
            for slot_index in range(inventory.size):
                if not self._slot_in_range(slot_index, slot_min, slot_max):
                    continue
                try:
                    item_stack = inventory.get_item(slot_index)
                except Exception as slot_e:
                    self._log(
                        "warning",
                        f"[ARCInventory] get_item(slot={slot_index}) failed: {slot_e}",
                    )
                    continue
                entry = self._build_item_entry(
                    player, item_stack, slot_index=slot_index
                )
                if entry:
                    items.append(entry)
            if include_armor:
                for attr in ARMOR_ATTRS:
                    if not hasattr(inventory, attr):
                        continue
                    try:
                        stack = getattr(inventory, attr, None)
                    except Exception:
                        continue
                    entry = self._build_item_entry(
                        player, stack, slot_index=attr, armor_slot=attr
                    )
                    if entry:
                        items.append(entry)
            return items
        except Exception as e:
            self._log(
                "error",
                f"[ARCInventory] Get player inventory error: {str(e)}\n{traceback.format_exc()}",
            )
            return []

    def has_item(
        self,
        player: Any,
        item_info: Dict[str, Any],
        *,
        slot_min: Optional[int] = None,
        slot_max: Optional[int] = None,
        include_armor: bool = False,
    ) -> bool:
        """检查玩家背包是否拥有至少 item_info 要求数量、类型、data、附魔、Lore 一致的物品。"""
        try:
            required_count = int(item_info.get("count", 0) or 0)
            if required_count <= 0:
                return True
            have = self.count_item(
                player,
                item_info,
                slot_min=slot_min,
                slot_max=slot_max,
                include_armor=include_armor,
            )
            return have >= required_count
        except Exception as e:
            self._log("error", f"[ARCInventory] Player has item check error: {str(e)}")
            return False

    def count_item(
        self,
        player: Any,
        item_info: Dict[str, Any],
        *,
        slot_min: Optional[int] = None,
        slot_max: Optional[int] = None,
        include_armor: bool = False,
    ) -> int:
        """统计与 item_info 匹配的物品总数（忽略 item_info.count）。"""
        try:
            inventory = player.inventory
            required_type = item_info["type"]
            required_data = item_info.get("data", 0)
            required_enchants = item_info.get("enchants", {})
            required_lore = item_info.get("lore", [])
            required_nbt_b64 = item_info.get("nbt_b64")
            total_count = 0
            for slot_index in range(inventory.size):
                if not self._slot_in_range(slot_index, slot_min, slot_max):
                    continue
                item_stack = inventory.get_item(slot_index)
                if not self._item_stack_matches_info(
                    item_stack,
                    required_type,
                    required_data,
                    required_enchants,
                    required_lore,
                    required_nbt_b64,
                ):
                    continue
                total_count += int(getattr(item_stack, "amount", 0) or 0)
            if include_armor:
                for attr in ARMOR_ATTRS:
                    if not hasattr(inventory, attr):
                        continue
                    item_stack = getattr(inventory, attr, None)
                    if not self._item_stack_matches_info(
                        item_stack,
                        required_type,
                        required_data,
                        required_enchants,
                        required_lore,
                        required_nbt_b64,
                    ):
                        continue
                    total_count += int(getattr(item_stack, "amount", 0) or 0)
            return int(total_count)
        except Exception as e:
            self._log("error", f"[ARCInventory] count_item error: {str(e)}")
            return 0

    def remove_item(
        self,
        player: Any,
        item_info: Dict[str, Any],
        *,
        partial: bool = False,
        slot_min: Optional[int] = None,
        slot_max: Optional[int] = None,
        include_armor: bool = False,
    ) -> int:
        """
        从玩家背包移除与 item_info 匹配的物品。
        默认不足则不改动并返回 0；partial=True 时尽可能扣除。
        返回实际移除数量（布尔判断仍兼容：>0 为真）。
        """
        try:
            inventory = player.inventory
            required_type = item_info["type"]
            required_count = int(item_info.get("count", 0) or 0)
            if required_count <= 0:
                return 0
            required_data = item_info.get("data", 0)
            required_enchants = item_info.get("enchants", {})
            required_lore = item_info.get("lore", [])
            required_nbt_b64 = item_info.get("nbt_b64")
            have = self.count_item(
                player,
                item_info,
                slot_min=slot_min,
                slot_max=slot_max,
                include_armor=include_armor,
            )
            if not partial and have < required_count:
                return 0
            remaining_to_remove = min(required_count, have)
            if remaining_to_remove <= 0:
                return 0
            removed_total = 0
            slots_to_modify: List[tuple] = []
            for slot_index in range(inventory.size):
                if remaining_to_remove <= 0:
                    break
                if not self._slot_in_range(slot_index, slot_min, slot_max):
                    continue
                item_stack = inventory.get_item(slot_index)
                if not self._item_stack_matches_info(
                    item_stack,
                    required_type,
                    required_data,
                    required_enchants,
                    required_lore,
                    required_nbt_b64,
                ):
                    continue
                remove_from_slot = min(remaining_to_remove, item_stack.amount)
                slots_to_modify.append((slot_index, item_stack, remove_from_slot))
                remaining_to_remove -= remove_from_slot
            for slot_index, original_stack, remove_count in slots_to_modify:
                new_amount = original_stack.amount - remove_count
                if new_amount <= 0:
                    inventory.set_item(slot_index, None)
                else:
                    original_stack.amount = new_amount
                    inventory.set_item(slot_index, original_stack)
                removed_total += remove_count
            if include_armor and remaining_to_remove > 0:
                for attr in ARMOR_ATTRS:
                    if remaining_to_remove <= 0:
                        break
                    if not hasattr(inventory, attr):
                        continue
                    item_stack = getattr(inventory, attr, None)
                    if not self._item_stack_matches_info(
                        item_stack,
                        required_type,
                        required_data,
                        required_enchants,
                        required_lore,
                        required_nbt_b64,
                    ):
                        continue
                    have_slot = int(getattr(item_stack, "amount", 0) or 0)
                    take = min(have_slot, remaining_to_remove)
                    if take >= have_slot:
                        setattr(inventory, attr, None)
                    else:
                        item_stack.amount = have_slot - take
                        setattr(inventory, attr, item_stack)
                    remaining_to_remove -= take
                    removed_total += take
            return int(removed_total)
        except Exception as e:
            self._log(
                "error", f"[ARCInventory] Remove item from player error: {str(e)}"
            )
            return 0

    def give_item(
        self,
        player: Any,
        item_info: Dict[str, Any],
        *,
        slot: Optional[int] = None,
        armor_slot: Optional[str] = None,
        reserved: int = 0,
        prefer_end: bool = False,
    ) -> bool:
        """向玩家背包发放物品（类型、数量、data；附魔/Lore/NBT 若 API 支持则应用）。"""
        given = self.give_item_count(
            player,
            item_info,
            slot=slot,
            armor_slot=armor_slot,
            reserved=reserved,
            prefer_end=prefer_end,
        )
        return given >= int(item_info.get("count", 0) or 0)

    def _resolve_max_stack(self, item_stack: Any) -> int:
        """读取物品真实最大堆叠数；镐等工具为 1，不可再硬编码 64。"""
        max_stack = getattr(item_stack, "max_stack_size", None)
        if max_stack is None:
            item_type = getattr(item_stack, "type", None)
            max_stack = getattr(item_type, "max_stack_size", None) if item_type else None
        try:
            max_stack = int(max_stack) if max_stack is not None else 64
        except Exception:
            max_stack = 64
        return max(1, max_stack)

    def _apply_item_meta_extras(self, item_stack: Any, item_info: Dict[str, Any]) -> bool:
        """用 enchants/lore 写入 ItemMeta；附魔使用 force=True，确保附魔书等可写入。"""
        enchants = item_info.get("enchants") or {}
        lore = item_info.get("lore") or []
        if not enchants and not lore:
            return False
        try:
            meta = item_stack.item_meta
            if meta is None:
                return False
            applied = False
            if enchants:
                for enchant_id, level in enchants.items():
                    try:
                        if hasattr(meta, "add_enchant"):
                            ok = meta.add_enchant(str(enchant_id), int(level), True)
                            applied = bool(ok) or applied
                    except TypeError:
                        # 旧 API 无 force 参数
                        try:
                            ok = meta.add_enchant(str(enchant_id), int(level))
                            applied = bool(ok) or applied
                        except Exception as e:
                            self._log(
                                "warning",
                                f"[ARCInventory] Failed to apply enchant {enchant_id}: {e}",
                            )
                    except Exception as e:
                        self._log(
                            "warning",
                            f"[ARCInventory] Failed to apply enchant {enchant_id}: {e}",
                        )
            if lore and hasattr(meta, "lore"):
                try:
                    meta.lore = list(lore)
                    applied = True
                except Exception as e:
                    self._log("warning", f"[ARCInventory] Failed to apply lore: {e}")
            if hasattr(item_stack, "set_item_meta"):
                item_stack.set_item_meta(meta)
            return applied
        except Exception as e:
            self._log("warning", f"[ARCInventory] Apply item meta: {e}")
            return False

    def _restore_item_nbt(self, item_stack: Any, nbt_b64: str) -> bool:
        """还原用户 NBT；成功返回 True。"""
        try:
            tag = _decode_nbt_b64(nbt_b64)
            if tag is None or not hasattr(item_stack, "nbt"):
                return False
            item_stack.nbt = tag
            # 校验：写回后应仍能序列化出非空 NBT
            check = self._serialize_item_nbt(item_stack)
            return bool(check)
        except Exception as e:
            self._log("warning", f"[ARCInventory] Restore item NBT failed: {e}")
            return False

    def _enchants_from_nbt_b64(self, nbt_b64: str) -> Dict[str, int]:
        """从已序列化的 nbt_b64 解析 ench，供 NBT 直接写回失败时的 ItemMeta 回退。"""
        if not nbt_b64:
            return {}
        try:
            from endstone.inventory import ItemStack

            tag = _decode_nbt_b64(nbt_b64)
            if tag is None:
                return {}
            probe = ItemStack(type="minecraft:enchanted_book", amount=1)
            if not hasattr(probe, "nbt"):
                return {}
            probe.nbt = tag
            return self._get_enchants_from_nbt(probe)
        except Exception:
            return {}

    def _prepare_give_stack(
        self, item_type_id: str, amount: int, item_data: int, item_info: Dict[str, Any]
    ) -> Any:
        """构造待发放的 ItemStack：遵守 max_stack，优先 NBT，失败则回退附魔/Lore。"""
        from endstone.inventory import ItemStack

        item_stack = ItemStack(type=item_type_id, amount=1, data=item_data)
        max_stack = self._resolve_max_stack(item_stack)
        give_amount = min(max(1, int(amount)), max_stack)
        item_stack.amount = give_amount

        nbt_b64 = item_info.get("nbt_b64")
        nbt_ok = False
        if nbt_b64:
            nbt_ok = self._restore_item_nbt(item_stack, nbt_b64)
            if not nbt_ok:
                self._log(
                    "warning",
                    f"[ARCInventory] NBT restore failed for {item_type_id}; fallback to enchants/lore.",
                )

        if not nbt_ok:
            fallback_info = dict(item_info)
            enchants = dict(fallback_info.get("enchants") or {})
            if not enchants and nbt_b64:
                enchants = self._enchants_from_nbt_b64(nbt_b64)
                if enchants:
                    fallback_info["enchants"] = enchants
            self._apply_item_meta_extras(item_stack, fallback_info)

        # 防止构造/还原过程改变数量
        if getattr(item_stack, "amount", give_amount) != give_amount:
            try:
                item_stack.amount = give_amount
            except Exception:
                pass
        return item_stack

    def _item_type_id(self, stack: Any) -> str:
        item_type = getattr(stack, "type", None)
        if item_type is None:
            return ""
        ident = getattr(item_type, "id", None)
        if ident:
            return str(ident)
        return str(item_type)

    def serialize_item(self, item_stack: Any) -> Optional[Dict[str, Any]]:
        """将 ItemStack 序列化为可存档的 item_info（含可选 nbt_b64 / enchants / lore）。"""
        if item_stack is None:
            return None
        try:
            if int(getattr(item_stack, "amount", 0) or 0) <= 0:
                return None
            type_id = self._item_type_id(item_stack)
            if not type_id or type_id == "minecraft:air":
                return None
            entry: Dict[str, Any] = {
                "type": type_id,
                "count": int(item_stack.amount),
                "data": int(getattr(item_stack, "data", 0) or 0),
            }
            nbt_b64 = self._serialize_item_nbt(item_stack)
            if nbt_b64:
                entry["nbt_b64"] = nbt_b64
            enchants = self._get_item_enchants(item_stack)
            if enchants:
                entry["enchants"] = enchants
            lore = self._get_item_lore(item_stack)
            if lore:
                entry["lore"] = lore
            return entry
        except Exception:
            return None

    def make_item_stack(self, item_info: Dict[str, Any]) -> Any:
        """由 item_info 构造 ItemStack（公开版 _prepare_give_stack）。"""
        type_id = str(item_info.get("type") or "")
        if not type_id or type_id == "minecraft:air":
            return None
        amount = max(1, int(item_info.get("count", 1) or 1))
        data = int(item_info.get("data", 0) or 0)
        return self._prepare_give_stack(type_id, amount, data, item_info)


    # ---------------- NBT 内容摘要（拍卖/邮件展示用） ----------------

    # 基岩版附魔数字 id → 中文名（Java/基岩通用 id 0-39）
    ENCHANT_NAMES = {
        0: "保护", 1: "火焰保护", 2: "摔落缓冲", 3: "爆炸保护", 4: "弹射物保护",
        5: "荆棘", 6: "水下呼吸", 7: "深海探索者", 8: "水下速掘", 9: "锋利",
        10: "亡灵杀手", 11: "节肢杀手", 12: "击退", 13: "火焰附加", 14: "抢夺",
        15: "效率", 16: "精准采集", 17: "耐久", 18: "时运", 19: "力量",
        20: "冲击", 21: "火矢", 22: "无限", 23: "海之眷顾", 24: "饵钓",
        25: "冰霜行者", 26: "经验修补", 27: "绑定诅咒", 28: "消失诅咒", 29: "穿刺",
        30: "激流", 31: "引雷", 32: "多重射击", 33: "快速装填", 34: "穿透",
        35: "灵魂疾行", 36: "迅捷潜行", 37: "风爆", 38: "致密", 39: "破甲",
    }
    ENCHANT_KEY_NAMES = {
        "protection": 0, "fire_protection": 1, "feather_falling": 2, "blast_protection": 3,
        "projectile_protection": 4, "thorns": 5, "respiration": 6, "depth_strider": 7,
        "aqua_affinity": 8, "sharpness": 9, "smite": 10, "bane_of_arthropods": 11,
        "knockback": 12, "fire_aspect": 13, "looting": 14, "efficiency": 15,
        "silk_touch": 16, "unbreaking": 17, "fortune": 18, "power": 19, "punch": 20,
        "flame": 21, "infinity": 22, "luck_of_the_sea": 23, "lure": 24,
        "frost_walker": 25, "mending": 26, "binding_curse": 27, "vanishing_curse": 28,
        "impaling": 29, "riptide": 30, "channeling": 31, "multishot": 32,
        "quick_charge": 33, "piercing": 34, "soul_speed": 35, "swift_sneak": 36,
        "wind_burst": 37, "density": 38, "breach": 39,
    }

    @staticmethod
    def _nbt_num(value, default=0) -> int:
        """{"@b"/"@s"/"@i"/"@l": n} 或裸数值 → int。"""
        if isinstance(value, dict):
            for v in value.values():
                try:
                    return int(v)
                except (TypeError, ValueError):
                    continue
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _enchant_display(self, eid, level: int) -> str:
        try:
            key = str(eid)
            num = int(key)
        except (TypeError, ValueError):
            k = str(eid or "").split(":")[-1]
            num = self.ENCHANT_KEY_NAMES.get(k)
        name = self.ENCHANT_NAMES.get(num if num is not None else -1)
        if not name:
            name = str(eid).split(":")[-1]
        romans = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]
        level_text = romans[level] if 0 < level < len(romans) else str(level)
        return f"{name} {level_text}" if level > 1 else name

    def _translate_contained_id(self, type_id: str) -> str:
        """容器内物品 id → 本地化名;失败回落短 id。"""
        if not type_id or not isinstance(type_id, str):
            return "未知物品"
        try:
            stack = self.make_item_stack({"type": type_id, "count": 1})
            if stack is not None and self._server is not None:
                key = getattr(stack.type, "translation_key", "")
                translated = self._server.language.translate(key, None, None)
                if translated and translated != key:
                    return str(translated)
        except Exception:
            pass
        return type_id.split(":")[-1]

    def summarize_item_nbt(self, item_info: Dict[str, Any], max_entries: int = 4) -> List[str]:
        """生成物品 NBT 摘要行(中文),最多展示前 max_entries 项,多余标注「等X项」。

        返回行列表(可能为空):
        - 内容物：名称×数量、… 等 X 项   （潜影盒等 NBT Items 列表）
        - 附魔：效率 III、耐久 III、… 等 X 项
        收纳袋(1.26)内容为组件化存储、不在 nbt_b64 中,无法摘要。
        """
        lines: List[str] = []
        try:
            import base64 as _b64
            tag = json.loads(_b64.b64decode(str(item_info.get("nbt_b64") or "") or "e30=")) \
                if item_info.get("nbt_b64") else {}
        except Exception:
            tag = {}
        if not isinstance(tag, dict):
            tag = {}

        # 1) 内容物:潜影盒等容器的 Items 列表
        items = tag.get("Items")
        if isinstance(items, list) and items:
            parts = []
            for it in items[:max_entries]:
                if not isinstance(it, dict):
                    continue
                count = self._nbt_num(it.get("Count"), 1)
                # 基岩真实数据 display/ench 就在条目根层（同 Items），有 tag 包装则是 Java 风格
                inner_tag = it.get("tag") if isinstance(it.get("tag"), dict) else it
                display = inner_tag.get("display") if isinstance(inner_tag.get("display"), dict) else {}
                name = display.get("Name") or it.get("CustomName") or None
                if not name:
                    name = self._translate_contained_id(str(it.get("Name") or it.get("id") or ""))
                parts.append(f"{name}×{int(count)}")
            line = "内容物：" + "、".join(parts)
            if len(items) > max_entries:
                line += f" 等{len(items)}项"
            lines.append(line)

        # 2) 附魔:优先 item_info["enchants"],否则 NBT 根层 ench(数字 id)
        ench_dict = item_info.get("enchants")
        if isinstance(ench_dict, dict) and ench_dict:
            pairs = [self._enchant_display(k, self._nbt_num(v, 1))
                     for k, v in list(ench_dict.items())[:max_entries]]
            total = len(ench_dict)
        else:
            inner_tag = tag.get("tag") if isinstance(tag.get("tag"), dict) else tag
            ench_list = inner_tag.get("ench")
            if not isinstance(ench_list, list):
                ench_list = []
            pairs = [self._enchant_display(self._nbt_num(e.get("id")),
                                           self._nbt_num(e.get("lvl"), 1))
                     for e in ench_list[:max_entries] if isinstance(e, dict)]
            total = len(ench_list)
        if pairs:
            line = "附魔：" + "、".join(pairs)
            if total > max_entries:
                line += f" 等{total}项"
            lines.append(line)
        return lines

    def set_slot(
        self,
        player: Any,
        slot: int,
        item_info: Optional[Dict[str, Any]],
    ) -> bool:
        """写入主背包指定槽位；item_info 为 None 则清空该格。"""
        try:
            inventory = player.inventory
            slot_i = int(slot)
            if slot_i < 0 or slot_i >= int(getattr(inventory, "size", 0) or 0):
                return False
            if not item_info:
                inventory.set_item(slot_i, None)
                return True
            stack = self.make_item_stack(item_info)
            inventory.set_item(slot_i, stack)
            return True
        except Exception as e:
            self._log("error", f"[ARCInventory] set_slot error: {e}")
            return False

    def set_armor_slot(
        self,
        player: Any,
        armor_slot: str,
        item_info: Optional[Dict[str, Any]],
    ) -> bool:
        """写入护甲/副手槽；item_info 为 None 则清空。"""
        attr = str(armor_slot or "").strip()
        if attr not in ARMOR_ATTRS:
            return False
        try:
            inventory = player.inventory
            if not hasattr(inventory, attr):
                return False
            if not item_info:
                setattr(inventory, attr, None)
                return True
            stack = self.make_item_stack(item_info)
            setattr(inventory, attr, stack)
            return True
        except Exception as e:
            self._log("error", f"[ARCInventory] set_armor_slot error: {e}")
            return False

    def _give_into_slot_range_end(
        self,
        player: Any,
        item_info: Dict[str, Any],
        *,
        reserved: int,
    ) -> int:
        """从背包末尾向前填充，避开前 reserved 格。"""
        inventory = player.inventory
        remaining = max(0, int(item_info.get("count", 0) or 0))
        if remaining <= 0:
            return 0
        item_type_id = str(item_info["type"])
        item_data = int(item_info.get("data", 0) or 0)
        size = int(getattr(inventory, "size", 0) or 0)
        start = max(int(reserved or 0), 0)
        given = 0
        for i in range(size - 1, start - 1, -1):
            if remaining <= 0:
                break
            try:
                existing = inventory.get_item(i)
            except Exception:
                continue
            existing_id = self._item_type_id(existing) if existing else ""
            existing_amt = int(getattr(existing, "amount", 0) or 0) if existing else 0
            if not existing or not existing_id or existing_amt <= 0:
                put_info = dict(item_info)
                put_info["count"] = remaining
                stack = self.make_item_stack(put_info)
                if stack is None:
                    break
                put = int(getattr(stack, "amount", 0) or 0)
                inventory.set_item(i, stack)
                remaining -= put
                given += put
                continue
            if existing_id != item_type_id:
                continue
            if int(getattr(existing, "data", 0) or 0) != item_data:
                continue
            max_stack = self._resolve_max_stack(existing)
            space = max_stack - existing_amt
            if space <= 0:
                continue
            put = min(space, remaining)
            existing.amount = existing_amt + put
            inventory.set_item(i, existing)
            remaining -= put
            given += put
        return int(given)

    def give_item_count(
        self,
        player: Any,
        item_info: Dict[str, Any],
        *,
        slot: Optional[int] = None,
        armor_slot: Optional[str] = None,
        reserved: int = 0,
        prefer_end: bool = False,
    ) -> int:
        """
        尝试向玩家背包发放物品，返回**实际成功发放的数量**（可能为部分）。
        - slot：写入主背包指定槽（覆盖该格；受 max_stack 限制）
        - armor_slot：写入护甲/副手属性名
        - prefer_end + reserved：从末尾向前填，避开前 reserved 格；仍放不下再 add_item
        按物品 max_stack_size 分堆发放（镐等不可堆叠会逐个发放）。
        """
        try:
            total_amount = int(item_info.get("count", 0) or 0)
            if total_amount <= 0:
                self._log("warning", f"[ARCInventory] Invalid item amount: {total_amount}")
                return 0
            armor = str(armor_slot or "").strip()
            if armor:
                if armor not in ARMOR_ATTRS:
                    return 0
                put_info = dict(item_info)
                put_info["count"] = min(total_amount, 1) if total_amount > 0 else 0
                # 护甲通常 1 件；仍按请求 count 写入 amount
                put_info["count"] = total_amount
                stack = self.make_item_stack(put_info)
                if stack is None:
                    return 0
                inventory = player.inventory
                if not hasattr(inventory, armor):
                    return 0
                setattr(inventory, armor, stack)
                return int(getattr(stack, "amount", 0) or 0)

            if slot is not None:
                stack = self.make_item_stack(dict(item_info))
                if stack is None:
                    return 0
                try:
                    player.inventory.set_item(int(slot), stack)
                except Exception:
                    return 0
                return int(getattr(stack, "amount", 0) or 0)

            given_total = 0
            remaining_to_give = total_amount
            if prefer_end:
                end_info = dict(item_info)
                end_info["count"] = remaining_to_give
                placed = self._give_into_slot_range_end(
                    player, end_info, reserved=int(reserved or 0)
                )
                given_total += placed
                remaining_to_give -= placed
                if remaining_to_give <= 0:
                    return int(given_total)

            inventory = player.inventory
            item_type_id = item_info["type"]
            item_data = item_info.get("data", 0)
            while remaining_to_give > 0:
                chunk_info = dict(item_info)
                chunk_info["count"] = remaining_to_give
                item_stack = self._prepare_give_stack(
                    item_type_id, remaining_to_give, item_data, chunk_info
                )
                stack_amount = int(getattr(item_stack, "amount", 0) or 0)
                if stack_amount <= 0:
                    break
                remaining_items = inventory.add_item(item_stack)
                if remaining_items:
                    try:
                        if hasattr(remaining_items, "get"):
                            first_remaining = remaining_items.get(0)
                        elif isinstance(remaining_items, dict):
                            first_remaining = next(iter(remaining_items.values()), None)
                        else:
                            first_remaining = (
                                remaining_items[0]
                                if isinstance(remaining_items, list)
                                and len(remaining_items) > 0
                                else None
                            )
                        remaining_amount = (
                            int(getattr(first_remaining, "amount", 0) or 0)
                            if first_remaining is not None
                            else 0
                        )
                        added_amount = max(0, stack_amount - remaining_amount)
                        if added_amount > 0:
                            given_total += added_amount
                        remaining_to_give -= added_amount
                        if added_amount == 0:
                            self._log(
                                "warning",
                                f"[ARCInventory] Player {player.name} inventory full",
                            )
                            break
                    except Exception as e:
                        self._log(
                            "warning",
                            f"[ARCInventory] Error calculating remaining: {e}",
                        )
                        break
                else:
                    remaining_to_give -= stack_amount
                    given_total += stack_amount
            return int(given_total)
        except Exception as e:
            self._log(
                "error", f"[ARCInventory] Give item to player error: {str(e)}"
            )
            return 0

    def clear_inventory(
        self,
        player: Any,
        *,
        include_contents: bool = True,
        include_armor: bool = True,
        slot_min: Optional[int] = None,
        slot_max: Optional[int] = None,
    ) -> bool:
        """清空主背包（可按槽范围）与/或护甲。"""
        try:
            inventory = player.inventory
            if include_contents:
                size = int(getattr(inventory, "size", 0) or 0)
                if slot_min is None and slot_max is None and hasattr(inventory, "clear"):
                    inventory.clear()
                else:
                    for i in range(size):
                        if not self._slot_in_range(i, slot_min, slot_max):
                            continue
                        try:
                            inventory.set_item(i, None)
                        except Exception:
                            continue
            if include_armor:
                for attr in ARMOR_ATTRS:
                    if not hasattr(inventory, attr):
                        continue
                    try:
                        setattr(inventory, attr, None)
                    except Exception:
                        continue
            return True
        except Exception as e:
            self._log("error", f"[ARCInventory] clear_inventory error: {e}")
            return False

    def snapshot_inventory(
        self,
        player: Any,
        *,
        include_armor: bool = True,
    ) -> Dict[str, Any]:
        """
        全量快照：含空槽（None）。
        返回 {"size": N, "slots": [...], "armor": {...}?}
        """
        out: Dict[str, Any] = {"size": 0, "slots": []}
        try:
            inventory = player.inventory
            size = int(getattr(inventory, "size", 0) or 0)
            slots: List[Optional[Dict[str, Any]]] = []
            for i in range(size):
                try:
                    slots.append(self.serialize_item(inventory.get_item(i)))
                except Exception:
                    slots.append(None)
            out["size"] = size
            out["slots"] = slots
            if include_armor:
                armor: Dict[str, Any] = {}
                for attr in ARMOR_ATTRS:
                    if not hasattr(inventory, attr):
                        continue
                    try:
                        armor[attr] = self.serialize_item(getattr(inventory, attr, None))
                    except Exception:
                        armor[attr] = None
                out["armor"] = armor
            return out
        except Exception as e:
            self._log("error", f"[ARCInventory] snapshot_inventory error: {e}")
            return out

    def restore_inventory(
        self,
        player: Any,
        snapshot: Dict[str, Any],
        *,
        include_armor: bool = True,
    ) -> bool:
        """按快照还原主背包与护甲（先清空对应区域）。

        支持两种格式：
        - 扁平：{"size", "slots", "armor"?}
        - 枪战兼容：{"inventory": {"slots":...}, "armor":...}
        """
        if not isinstance(snapshot, dict):
            return False
        try:
            inventory = player.inventory
            nested = snapshot.get("inventory")
            if isinstance(nested, dict) and ("slots" in nested or "size" in nested):
                slots = nested.get("slots")
                armor = snapshot.get("armor") if "armor" in snapshot else nested.get("armor")
            else:
                slots = snapshot.get("slots")
                armor = snapshot.get("armor")

            if slots is not None:
                size = int(getattr(inventory, "size", 0) or 0)
                if hasattr(inventory, "clear"):
                    inventory.clear()
                for i, slot_data in enumerate(slots or []):
                    if i >= size:
                        break
                    try:
                        stack = self.make_item_stack(slot_data) if slot_data else None
                        inventory.set_item(i, stack)
                    except Exception:
                        continue
            if include_armor and isinstance(armor, dict):
                for attr in ARMOR_ATTRS:
                    if not hasattr(inventory, attr):
                        continue
                    try:
                        data = armor.get(attr)
                        setattr(
                            inventory,
                            attr,
                            self.make_item_stack(data) if data else None,
                        )
                    except Exception:
                        try:
                            setattr(inventory, attr, None)
                        except Exception:
                            pass
            return True
        except Exception as e:
            self._log("error", f"[ARCInventory] restore_inventory error: {e}")
            return False
