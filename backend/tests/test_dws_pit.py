"""DWS时点汇总测试：按业务时点关联DWD明细与维度历史，生成时点汇总表。

验证：
- 按业务时点关联维度历史，保留生效维度键与统计窗口数据结果
- 业务时点、维度生效区间和汇总窗口必须一致（窗口外时点/未命中均拦截）
- 关联错版本时同一明细会计入两个维度键，生成时拦截且不保存
- 并发写入同一汇总窗口只保留一个完整版本
- 规则按阈值上下限判定，越界值不允许保存
- 迟到维度更正：迟到接管生成新汇总窗口 / 保留原窗口附受影响维度键清单
- 两种处理冲突按处理先后比较，后者优先（优先级结论待定）
- 维度建模跨模块读取DWS时点汇总表，规则阈值顺着汇总层管理调用链
"""
from __future__ import annotations

import threading

from app.seed import SEED_DIM_HISTORY, SEED_ROWS
from app.store import store
from app.services.dim_model import DimModelService
from app.services.dws_layer import DwsPitService

DWS_USR_ID = 3  # seed 中 DWS_USR_001（已配置维度模型 DIM_USER_001）
DIM_USER_ID = 2  # seed 中 DIM_USER_001
WINDOW = "2026-11-01/2026-11-03"


def reset_store():
    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
    store._table_data.clear()
    store._versions.clear()
    store._lineage_edges.clear()
    store._quality_results.clear()
    store._snapshot_data.clear()
    store._archive_records.clear()
    store._backfill_partitions.clear()
    store._dim_history = {code: [dict(r) for r in rows] for code, rows in SEED_DIM_HISTORY.items()}
    store._dws_pit_results.clear()
    store._dws_pit_locks.clear()
    store._late_dim_handlings.clear()


def prepare_user_dwd():
    """三条用户明细，业务时点 2026-11-01，落在汇总窗口内。"""
    store.set_table_rows("dwd_user_detail", [
        {"user_id": "U0001", "user_level": "普通", "dt": "2026-11-01"},
        {"user_id": "U0002", "user_level": "VIP", "dt": "2026-11-01"},
        {"user_id": "U0003", "user_level": "普通", "dt": "2026-11-01"},
    ])


class TestPitAggregate:
    def setup_method(self):
        reset_store()

    def test_pit_aggregate_keeps_effective_dim_key(self):
        """按业务时点关联维度历史，汇总表保留生效维度键和统计窗口数据结果。"""
        prepare_user_dwd()
        pit = DwsPitService()
        result = pit.generate(DWS_USR_ID)
        assert result["ok"], result.get("message")
        rows = result["result"]["rows"]
        assert len(rows) == 3
        # U0001 明细当前值是"普通"，但 2026-11-01 维度历史生效版本是 v2(VIP)
        u1 = next(r for r in rows if r["自然键"] == "U0001")
        assert u1["维度键"] == "U0001#v2"
        assert u1["生效区间"] == "2026-11-01/2099-12-31"
        assert u1["汇总窗口"] == WINDOW
        assert u1["度量值"] == 1
        # 结果写入时点汇总数据表
        assert store.table_rows("dws_user_daily_pit") == rows

    def test_window_mismatch_rejected(self):
        """业务时点超出汇总窗口：时点与窗口不一致，拒绝保存。"""
        store.set_table_rows("dwd_user_detail", [
            {"user_id": "U0002", "user_level": "VIP", "dt": "2026-12-01"},
        ])
        pit = DwsPitService()
        result = pit.generate(DWS_USR_ID)
        assert not result["ok"]
        assert "窗口" in result["message"]
        assert store.get_pit_results(DWS_USR_ID) == {}

    def test_no_effective_interval_rejected(self):
        """业务时点未命中任何维度生效区间：维度历史与窗口不一致，拒绝。"""
        store.set_table_rows("dwd_user_detail", [
            {"user_id": "U9999", "user_level": "普通", "dt": "2026-11-01"},
        ])
        pit = DwsPitService()
        result = pit.generate(DWS_USR_ID)
        assert not result["ok"]
        assert "未命中" in result["message"]
        assert store.get_pit_results(DWS_USR_ID) == {}

    def test_wrong_version_double_count_blocked(self):
        """关联错版本：同一明细命中两个维度键会被计入两次，生成时拦截。"""
        prepare_user_dwd()
        # 制造错版本：U0001 存在一个与 v2 区间重叠的历史版本
        store.add_dim_history_version("DIM_USER_001", {
            "自然键": "U0001", "维度键": "U0001#vX", "版本": "vX",
            "生效日期": "2026-10-15", "失效日期": "2026-11-15",
            "属性": {"user_level": "SVIP"}, "来源": "初始",
        })
        pit = DwsPitService()
        result = pit.generate(DWS_USR_ID)
        assert not result["ok"]
        assert "错版本" in result["message"]
        assert "两个维度键" in result["message"]
        # 拦截后不保存任何版本
        assert store.get_pit_results(DWS_USR_ID) == {}

    def test_threshold_out_of_range_not_saved(self):
        """规则按阈值上下限判定：越界值不允许保存。"""
        prepare_user_dwd()
        entry = store.find("dws_layer", DWS_USR_ID)
        entry["阈值上界"] = "0"  # COUNT=1 必然越界
        pit = DwsPitService()
        result = pit.generate(DWS_USR_ID)
        assert not result["ok"]
        assert "越界" in result["message"]
        assert result["越界行"]
        assert store.get_pit_results(DWS_USR_ID) == {}

    def test_condition_standard_filters_scope(self):
        """条件标准限定口径范围：不满足条件的明细不进入汇总。"""
        prepare_user_dwd()
        entry = store.find("dws_layer", DWS_USR_ID)
        entry["条件标准"] = "user_level=VIP"
        pit = DwsPitService()
        result = pit.generate(DWS_USR_ID)
        assert result["ok"], result.get("message")
        rows = result["result"]["rows"]
        assert {r["自然键"] for r in rows} == {"U0002"}

    def test_concurrent_writes_keep_single_complete_version(self):
        """并发写入同一汇总窗口：只保留一个完整版本。"""
        prepare_user_dwd()
        pit = DwsPitService()
        outcomes = []

        def worker():
            outcomes.append(pit.generate(DWS_USR_ID))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert all(r["ok"] for r in outcomes)
        saved = store.get_pit_results(DWS_USR_ID)
        assert list(saved.keys()) == [WINDOW]
        version = saved[WINDOW]
        # 完整版本：3个生效维度键 + 3条明细映射，不多不少
        assert len(version["rows"]) == 3
        assert len(version["明细映射"]) == 3

    def test_verify_item_by_item(self):
        """逐项核对：每个明细只命中一个维度键，窗口与生效区间逐项可核对。"""
        prepare_user_dwd()
        pit = DwsPitService()
        pit.generate(DWS_USR_ID)
        verify = pit.verify(DWS_USR_ID)
        assert verify["ok"]
        assert verify["全部通过"], verify.get("message")
        assert len(verify["核对项"]) == 3
        for item in verify["核对项"]:
            assert item["唯一命中"]
            assert item["时点在窗口内"]
            assert item["时点在生效区间内"]


class TestLateDimCorrection:
    def setup_method(self):
        reset_store()
        prepare_user_dwd()
        self.pit = DwsPitService()
        generated = self.pit.generate(DWS_USR_ID)
        assert generated["ok"]

    def _correction(self, strategy: str) -> dict:
        return self.pit.late_dim_correction(DWS_USR_ID, {
            "自然键": "U0002",
            "生效日期": "2026-11-01",
            "失效日期": "2099-12-31",
            "属性": {"user_level": "SVIP"},
            "策略": strategy,
        })

    def test_takeover_generates_new_window_version(self):
        """迟到接管：生成迟到版本接管后的新汇总窗口。"""
        result = self._correction("迟到接管")
        assert result["ok"], result.get("message")
        outcome = result["处理结果"]["窗口处理"][0]
        assert outcome["窗口"] == WINDOW
        assert outcome["处理"] == "迟到接管"
        saved = store.get_pit_results(DWS_USR_ID)[WINDOW]
        u2 = next(r for r in saved["rows"] if r["自然键"] == "U0002")
        assert u2["维度键"] == "U0002#v2"  # 接管后的新维度键
        assert saved["迟到接管"] is True
        assert saved["接管维度键"] == "U0002#v2"
        # 接管后同一时点仍只命中一个版本（旧版本已被截断）
        verify = self.pit.verify(DWS_USR_ID)
        assert verify["全部通过"]

    def test_keep_original_window_with_affected_keys(self):
        """保留原窗口：兼容既有时点汇总表，附受影响维度键清单。"""
        result = self._correction("保留原窗口")
        assert result["ok"], result.get("message")
        saved = store.get_pit_results(DWS_USR_ID)[WINDOW]
        u2 = next(r for r in saved["rows"] if r["自然键"] == "U0002")
        assert u2["维度键"] == "U0002#v1"  # 原业务窗口保留不变
        assert saved["保留原窗口"] is True
        assert saved["受影响维度键"] == ["U0002#v1"]

    def test_no_overlap_only_registers_history(self):
        """迟到更正与已发布业务窗口无重叠：只登记维度历史，不动汇总窗口。"""
        result = self.pit.late_dim_correction(DWS_USR_ID, {
            "自然键": "U0002",
            "生效日期": "2027-01-01",
            "失效日期": "2099-12-31",
            "属性": {"user_level": "SVIP"},
            "策略": "迟到接管",
        })
        assert result["ok"]
        assert "无重叠" in result["message"]
        saved = store.get_pit_results(DWS_USR_ID)[WINDOW]
        assert "迟到接管" not in saved

    def test_conflict_compare_order(self):
        """两种处理冲突：按处理先后可比较，当前生效为后处理，优先级结论待定。"""
        first = self._correction("保留原窗口")
        assert first["ok"]
        second = self._correction("迟到接管")
        assert second["ok"]
        compared = self.pit.compare_handlings(DWS_USR_ID)
        assert compared["ok"]
        assert len(compared["处理先后"]) == 2
        assert compared["处理先后"][0]["策略"] == "保留原窗口"
        assert compared["处理先后"][1]["策略"] == "迟到接管"
        assert compared["当前生效"] == "迟到接管"
        assert "尚无" in compared["优先级结论"]
        # 后处理（迟到接管）生效：窗口已被重算为第二次更正的维度键
        saved = store.get_pit_results(DWS_USR_ID)[WINDOW]
        u2 = next(r for r in saved["rows"] if r["自然键"] == "U0002")
        assert u2["维度键"] == "U0002#v3"  # 第一次更正产生v2，第二次（接管）产生v3
        assert saved["迟到接管"] is True


class TestDimModelCrossModuleRead:
    def setup_method(self):
        reset_store()
        prepare_user_dwd()
        DwsPitService().generate(DWS_USR_ID)

    def test_dim_model_reads_dws_pit_with_rules(self):
        """维度建模跨模块读取DWS时点汇总表，规则阈值顺着汇总层管理调用链。"""
        svc = DimModelService()
        result = svc.get_dws_pit_results(DIM_USER_ID)
        assert result["ok"], result.get("message")
        items = result["汇总结果"]
        assert len(items) == 1
        item = items[0]
        assert item["汇总表"] == "DWS_USR_001"
        assert item["规则阈值"]["阈值下界"] == "0"
        assert item["规则阈值"]["阈值上界"] == "100"
        windows = item["时点汇总结果"]
        assert WINDOW in windows
        u1 = next(r for r in windows[WINDOW]["rows"] if r["自然键"] == "U0001")
        assert u1["维度键"] == "U0001#v2"

    def test_dim_model_without_dws_link(self):
        """未关联时点汇总的维度模型：返回空结果而不是报错。"""
        svc = DimModelService()
        result = svc.get_dws_pit_results(1)  # DIM_DATE_001 未关联
        assert result["ok"]
        assert result["汇总结果"] == []
