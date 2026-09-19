# -*- coding: utf-8 -*-
"""弧光背包管理器：为弧光系列插件提供统一的背包读写 API。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from endstone import Player
from endstone.plugin import Plugin

from endstone_arc_inventory.InventoryManager import InventoryManager


class ARCInventoryPlugin(Plugin):
    """
    Plugin id: arc_inventory
    其他插件：server.get_plugin("arc_inventory") 后调用 api_*。
    """

    api_version = "0.10"
    prefix = "ARCInventory"
    load_before = ["arc_button_shop", "arc_sign_shop"]

    def __init__(self):
        super().__init__()
        self.inventory_manager: Optional[InventoryManager] = None

    def _safe_log(self, level: str, message: str) -> None:
        if hasattr(self, "logger") and self.logger is not None:
            fn = getattr(self.logger, level.lower(), None)
            if callable(fn):
                fn(message)
                return
            self.logger.info(message)
        else:
            print(f"[{level.upper()}] {message}")

    def on_load(self) -> None:
        if self.inventory_manager is None:
            self.inventory_manager = InventoryManager(self)
        self._safe_log("info", "[ARCInventory] on_load")

    def on_enable(self) -> None:
        if self.inventory_manager is None:
            self.inventory_manager = InventoryManager(self)
        self._safe_log("info", "[ARCInventory] 已启用，可供其它插件通过 api_* 操作玩家背包。")

    def on_disable(self) -> None:
        self._safe_log("info", "[ARCInventory] on_disable")

    def _mgr(self) -> Optional[InventoryManager]:
        return self.inventory_manager

    # ---------- 对外 API ----------

    def api_get_inventory_items(
        self,
        player: Player,
        *,
        include_armor: bool = False,
        slot_min: Optional[int] = None,
        slot_max: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        列出玩家背包有效物品。
        每项含 type / name / count / data / enchants / lore / slot_index，可能含 nbt_b64。
        include_armor=True 时追加护甲/副手（带 armor_slot）。
        slot_min / slot_max 过滤主背包槽位（含端点），便于只读热键栏。
        """
        mgr = self._mgr()
        if mgr is None or player is None:
            return []
        return mgr.get_inventory_items(
            player,
            include_armor=include_armor,
            slot_min=slot_min,
            slot_max=slot_max,
        )

    def api_has_item(
        self,
        player: Player,
        item_info: Dict[str, Any],
        *,
        slot_min: Optional[int] = None,
        slot_max: Optional[int] = None,
        include_armor: bool = False,
    ) -> bool:
        """是否拥有与 item_info 匹配（类型/数量/data/附魔/Lore/NBT）的物品。"""
        mgr = self._mgr()
        if mgr is None or player is None or not isinstance(item_info, dict):
            return False
        return bool(
            mgr.has_item(
                player,
                item_info,
                slot_min=slot_min,
                slot_max=slot_max,
                include_armor=include_armor,
            )
        )

    def api_remove_item(
        self,
        player: Player,
        item_info: Dict[str, Any],
        *,
        partial: bool = False,
        slot_min: Optional[int] = None,
        slot_max: Optional[int] = None,
        include_armor: bool = False,
    ) -> int:
        """
        按 item_info 从背包移除匹配物品。
        默认不足则失败返回 0；partial=True 时尽可能扣除。
        返回实际移除数量（作布尔判断时 >0 仍为真，兼容旧调用）。
        """
        mgr = self._mgr()
        if mgr is None or player is None or not isinstance(item_info, dict):
            return 0
        try:
            return int(
                mgr.remove_item(
                    player,
                    item_info,
                    partial=partial,
                    slot_min=slot_min,
                    slot_max=slot_max,
                    include_armor=include_armor,
                )
                or 0
            )
        except Exception:
            return 0

    def api_give_item(
        self,
        player: Player,
        item_info: Dict[str, Any],
        *,
        slot: Optional[int] = None,
        armor_slot: Optional[str] = None,
        reserved: int = 0,
        prefer_end: bool = False,
    ) -> bool:
        """发放物品；足额成功返回 True。支持定点槽 / 护甲槽 / 避开热键从末尾填。"""
        mgr = self._mgr()
        if mgr is None or player is None or not isinstance(item_info, dict):
            return False
        return bool(
            mgr.give_item(
                player,
                item_info,
                slot=slot,
                armor_slot=armor_slot,
                reserved=reserved,
                prefer_end=prefer_end,
            )
        )

    def api_give_item_count(
        self,
        player: Player,
        item_info: Dict[str, Any],
        *,
        slot: Optional[int] = None,
        armor_slot: Optional[str] = None,
        reserved: int = 0,
        prefer_end: bool = False,
    ) -> int:
        """尝试发放，返回实际入包数量。参数同 api_give_item。"""
        mgr = self._mgr()
        if mgr is None or player is None or not isinstance(item_info, dict):
            return 0
        try:
            return int(
                mgr.give_item_count(
                    player,
                    item_info,
                    slot=slot,
                    armor_slot=armor_slot,
                    reserved=reserved,
                    prefer_end=prefer_end,
                )
                or 0
            )
        except Exception:
            return 0

    def api_set_slot(
        self,
        player: Player,
        slot: int,
        item_info: Optional[Dict[str, Any]],
    ) -> bool:
        """写入主背包指定槽；item_info 为 None 清空该格。"""
        mgr = self._mgr()
        if mgr is None or player is None:
            return False
        return bool(mgr.set_slot(player, slot, item_info))

    def api_set_armor_slot(
        self,
        player: Player,
        armor_slot: str,
        item_info: Optional[Dict[str, Any]],
    ) -> bool:
        """写入护甲/副手（helmet/chestplate/leggings/boots/item_in_off_hand）。"""
        mgr = self._mgr()
        if mgr is None or player is None:
            return False
        return bool(mgr.set_armor_slot(player, armor_slot, item_info))

    def api_clear_inventory(
        self,
        player: Player,
        *,
        include_contents: bool = True,
        include_armor: bool = True,
        slot_min: Optional[int] = None,
        slot_max: Optional[int] = None,
    ) -> bool:
        """清空主背包与/或护甲；可用 slot_min/slot_max 限定主背包范围。"""
        mgr = self._mgr()
        if mgr is None or player is None:
            return False
        return bool(
            mgr.clear_inventory(
                player,
                include_contents=include_contents,
                include_armor=include_armor,
                slot_min=slot_min,
                slot_max=slot_max,
            )
        )

    def api_snapshot_inventory(
        self,
        player: Player,
        *,
        include_armor: bool = True,
    ) -> Dict[str, Any]:
        """全量快照（含空槽 None）。返回 size/slots，可选 armor。"""
        mgr = self._mgr()
        if mgr is None or player is None:
            return {"size": 0, "slots": []}
        return mgr.snapshot_inventory(player, include_armor=include_armor)

    def api_restore_inventory(
        self,
        player: Player,
        snapshot: Dict[str, Any],
        *,
        include_armor: bool = True,
    ) -> bool:
        """按快照还原；兼容扁平格式与 {inventory, armor} 嵌套格式。"""
        mgr = self._mgr()
        if mgr is None or player is None or not isinstance(snapshot, dict):
            return False
        return bool(
            mgr.restore_inventory(player, snapshot, include_armor=include_armor)
        )

    def api_serialize_item(self, item_stack: Any) -> Optional[Dict[str, Any]]:
        """ItemStack → item_info dict。"""
        mgr = self._mgr()
        if mgr is None:
            return None
        return mgr.serialize_item(item_stack)

    def api_make_item_stack(self, item_info: Dict[str, Any]) -> Any:
        """item_info dict → ItemStack。"""
        mgr = self._mgr()
        if mgr is None or not isinstance(item_info, dict):
            return None
        return mgr.make_item_stack(item_info)

    def api_summarize_item(self, item_info: Dict[str, Any], max_entries: int = 4) -> List[str]:
        """生成物品 NBT 内容物/附魔中文摘要行(拍卖、邮件等展示用),见 InventoryManager.summarize_item_nbt。"""
        mgr = self._mgr()
        if mgr is None or not isinstance(item_info, dict):
            return []
        try:
            return list(mgr.summarize_item_nbt(item_info, max_entries=int(max_entries)) or [])
        except Exception:
            return []

    def api_get_inventory_manager(self) -> Optional[InventoryManager]:
        """高级用法：直接拿到 InventoryManager 实例（与按钮商店原先用法一致）。"""
        return self._mgr()
