"""汇总层管理业务规则：消费DWD明细数据，按维度聚合生成汇总表，支持版本和血缘。"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app.store import store

MODULE = "dws_layer"
LIST_FIELDS = ["表编号", "统计粒度", "汇总维度", "度量字段", "聚合函数", "时间范围", "刷新周期", "创建批次"]
REQUIRED_FIELDS = ["表编号", "统计粒度", "汇总维度"]
STATUS_ORDER = ["草稿", "待校验", "已上线", "已下线"]
ACTION_RULES = {"提交校验": "待校验", "确认上线": "已上线", "标记下线": "已下线"}
NEGATIVE_ACTIONS = []
# 时点汇总扩展字段：不进列表页，但登记时透传保存
PIT_EXTRA_FIELDS = ["来源明细表", "维度模型", "自然键列", "业务时点列", "阈值下界", "阈值上界", "条件标准"]


class DwsLayerService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("表编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        for extra in PIT_EXTRA_FIELDS:
            if values.get(extra) is not None:
                entry[extra] = values[extra]
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"汇总表 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于汇总层管理可执行范围"
        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        store.save_version(MODULE, entry_id, entry)
        return entry, f"汇总表已{action}"

    def aggregate_from_dwd(self, entry_id: int) -> dict[str, Any]:
        """从关联DWD表读取数据，按汇总维度聚合。"""
        entry = self.get_entry(entry_id)
        if not entry:
            return {"ok": False, "message": "汇总表不存在"}
        dwd_code = entry.get("来源明细表", "")
        dwd_entry = None
        for row in store.rows("dwd_layer"):
            if row.get("表编号") == dwd_code or row.get("表名称") == dwd_code:
                dwd_entry = row
                break
        if not dwd_entry:
            return {"ok": False, "message": f"来源明细表 {dwd_code} 不存在"}
        dwd_table = dwd_entry.get("表名称", "")
        dwd_data = store.table_rows(dwd_table)
        if not dwd_data:
            from app.services.dwd_layer import DwdLayerService
            dwd_svc = DwdLayerService()
            dwd_svc.build_dwd_from_ods(dwd_entry["id"])
            dwd_data = store.table_rows(dwd_table)
        dims = [d.strip() for d in entry.get("汇总维度", "").split(",")]
        measure = entry.get("度量字段", "")
        agg_func = entry.get("聚合函数", "SUM")
        result = self._do_aggregate(dwd_data, dims, measure, agg_func)
        dws_table = entry.get("表名称", f"dws_{entry_id}")
        store.set_table_rows(dws_table, result)
        store.save_version(MODULE, entry_id, entry)
        # 注册血缘
        store.add_lineage(dwd_table, dws_table, ",".join(dims),
                          transform_logic=f"{agg_func}({measure}) GROUP BY {','.join(dims)}",
                          batch_id=entry.get("创建批次", ""))
        return {"ok": True, "message": f"汇总表 {dws_table} 已生成，共{len(result)}行", "rows": result}

    def _do_aggregate(self, data: list[dict[str, Any]], dims: list[str], measure: str, agg: str) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for row in data:
            key_parts = []
            for d in dims:
                key_parts.append(str(row.get(d, "")))
            key = "|".join(key_parts)
            if key not in groups:
                groups[key] = {d: row.get(d) for d in dims}
                groups[key]["_values"] = []
            try:
                groups[key]["_values"].append(float(row.get(measure, 0) or 0))
            except Exception:
                groups[key]["_values"].append(0.0)
        result = []
        for key, g in groups.items():
            out = {d: g[d] for d in dims}
            vals = g["_values"]
            if agg == "SUM":
                out[f"{measure}_agg"] = sum(vals)
            elif agg == "COUNT":
                out[f"{measure}_agg"] = len(vals)
            elif agg == "AVG":
                out[f"{measure}_agg"] = sum(vals) / len(vals) if vals else 0
            elif agg == "MAX":
                out[f"{measure}_agg"] = max(vals) if vals else 0
            elif agg == "MIN":
                out[f"{measure}_agg"] = min(vals) if vals else 0
            else:
                out[f"{measure}_agg"] = sum(vals)
            result.append(out)
        return result

    def get_table_data(self, table_name: str) -> list[dict[str, Any]]:
        return store.table_rows(table_name)


class DwsPitService:
    """DWS时点汇总：按业务时点关联DWD明细与维度历史，生成保留生效维度键的时点汇总表。

    一致性约束：业务时点、维度生效区间和汇总窗口必须一致——
    每条明细的业务时点必须落在汇总窗口内，且恰好命中一个维度生效区间；
    关联错版本会让同一明细计入两个维度键，生成时直接拦截，不允许保存。
    """

    def generate(self, entry_id: int) -> dict[str, Any]:
        """生成时点汇总。并发写入时按汇总表串行，同一窗口只保留一个完整版本。"""
        lock = store.pit_lock(entry_id)
        with lock:
            return self._generate_locked(entry_id)

    def _generate_locked(self, entry_id: int) -> dict[str, Any]:
        entry = store.find(MODULE, entry_id)
        if not entry:
            return {"ok": False, "message": "汇总表不存在"}
        dim_code = str(entry.get("维度模型", "") or "")
        if not dim_code:
            return {"ok": False, "message": "汇总表未配置维度模型，无法按维度历史时点聚合"}
        nk_col = str(entry.get("自然键列", "") or "")
        ts_col = str(entry.get("业务时点列", "") or "dt")
        window = str(entry.get("时间范围", "") or "")
        parsed = self._parse_window(window)
        if parsed is None:
            return {"ok": False, "message": f"汇总窗口「{window}」无法解析，应为 起/止 格式"}
        win_start, win_end = parsed
        # 读取DWD明细（没有数据时先按既有链路构建）
        dwd_table, dwd_rows, error = self._load_dwd_rows(entry)
        if error:
            return {"ok": False, "message": error}
        # 条件标准过滤（口径的一部分）
        dwd_rows = self._apply_condition(dwd_rows, str(entry.get("条件标准", "") or ""))
        history = store.dim_history(dim_code)
        if not history:
            return {"ok": False, "message": f"维度模型 {dim_code} 没有维度历史，无法时点关联"}
        # 逐条明细按时点关联维度历史
        mappings: list[dict[str, Any]] = []
        for row in dwd_rows:
            ts = str(row.get(ts_col, "") or "")[:10]
            if not ts:
                return {"ok": False, "message": f"明细缺少业务时点列 {ts_col}，无法对齐汇总窗口"}
            if not (win_start <= ts <= win_end):
                return {"ok": False,
                        "message": f"业务时点 {ts} 不在汇总窗口 {window} 内，时点与窗口不一致"}
            nk = str(row.get(nk_col, "") or "")
            hits = [h for h in history
                    if str(h.get("自然键")) == nk
                    and str(h.get("生效日期")) <= ts <= str(h.get("失效日期"))]
            if not hits:
                return {"ok": False,
                        "message": f"明细 {nk}@{ts} 未命中任何维度生效区间，维度历史与汇总窗口不一致"}
            if len(hits) > 1:
                keys = "、".join(str(h.get("维度键")) for h in hits)
                return {"ok": False,
                        "message": f"关联错版本：明细 {nk}@{ts} 命中 {len(hits)} 个维度键（{keys}），"
                                   "同一明细会被计入两个维度键，已拦截"}
            hit = hits[0]
            mappings.append({
                "明细键": f"{nk}@{ts}",
                "自然键": nk,
                "业务时点": ts,
                "维度键": str(hit.get("维度键")),
                "维度版本": str(hit.get("版本", "")),
                "生效区间": f"{hit.get('生效日期')}/{hit.get('失效日期')}",
                "命中数": len(hits),
                "度量原值": self._to_float(row.get(entry.get("度量字段", ""))),
            })
        # 按生效维度键聚合，保留统计窗口数据结果
        rows = self._aggregate(entry, mappings, window)
        # 规则按阈值上下限判定：口径范围不允许保存越界值
        violations = self._threshold_violations(entry, rows)
        if violations:
            return {"ok": False,
                    "message": f"聚合结果存在 {len(violations)} 行越界值，口径范围不允许保存",
                    "越界行": violations}
        dws_table = f"{entry.get('表名称', f'dws_{entry_id}')}_pit"
        result = {
            "汇总表": entry.get("表编号", ""),
            "表名称": dws_table,
            "维度模型": dim_code,
            "汇总窗口": window,
            "生成时间": datetime.now().isoformat(),
            "rows": rows,
            "明细映射": mappings,
        }
        # 同一窗口只保留一个完整版本：整体覆盖写
        store.save_pit_result(entry_id, window, result)
        store.set_table_rows(dws_table, rows)
        store.save_version(MODULE, entry_id, entry)
        store.add_lineage(dwd_table, dws_table, nk_col,
                          transform_logic=f"时点关联{dim_code}+{entry.get('聚合函数', 'SUM')}({entry.get('度量字段', '')})",
                          batch_id=entry.get("创建批次", ""))
        return {"ok": True,
                "message": f"DWS时点汇总表 {dws_table} 已生成，窗口 {window}，共{len(rows)}个生效维度键",
                "result": result}

    def get_pit_result(self, entry_id: int) -> dict[str, Any]:
        """读取该汇总表全部已发布窗口的时点汇总结果。"""
        entry = store.find(MODULE, entry_id)
        if not entry:
            return {"ok": False, "message": "汇总表不存在"}
        results = store.get_pit_results(entry_id)
        return {"ok": True, "汇总表": entry.get("表编号", ""), "窗口结果": results}

    def verify(self, entry_id: int) -> dict[str, Any]:
        """逐项核对：每个明细只命中一个维度键，历史窗口与维度生效区间逐项可核对。"""
        entry = store.find(MODULE, entry_id)
        if not entry:
            return {"ok": False, "message": "汇总表不存在"}
        results = store.get_pit_results(entry_id)
        if not results:
            return {"ok": False, "message": "尚未生成时点汇总，请先生成"}
        checks: list[dict[str, Any]] = []
        for window, result in results.items():
            parsed = self._parse_window(window)
            for m in result.get("明细映射", []):
                ts = m.get("业务时点", "")
                eff = str(m.get("生效区间", ""))
                eff_parsed = self._parse_window(eff)
                in_window = bool(parsed and parsed[0] <= ts <= parsed[1])
                in_effective = bool(eff_parsed and eff_parsed[0] <= ts <= eff_parsed[1])
                checks.append({
                    "汇总窗口": window,
                    "明细键": m.get("明细键", ""),
                    "业务时点": ts,
                    "维度键": m.get("维度键", ""),
                    "维度版本": m.get("维度版本", ""),
                    "生效区间": eff,
                    "命中数": m.get("命中数", 0),
                    "唯一命中": m.get("命中数") == 1,
                    "时点在窗口内": in_window,
                    "时点在生效区间内": in_effective,
                })
        all_ok = all(c["唯一命中"] and c["时点在窗口内"] and c["时点在生效区间内"] for c in checks)
        return {"ok": True, "全部通过": all_ok, "核对项": checks,
                "message": "逐项核对通过：每个明细只命中一个维度键" if all_ok else "存在核对不通过项"}

    def late_dim_correction(self, entry_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """迟到维度更正：与已发布业务窗口重叠时按策略处理。

        - 迟到接管：用接管后的维度历史重新生成该窗口的新汇总版本；
        - 保留原窗口：兼容既有时点汇总表，保留原业务窗口并附受影响维度键清单。
        """
        entry = store.find(MODULE, entry_id)
        if not entry:
            return {"ok": False, "message": "汇总表不存在"}
        dim_code = str(entry.get("维度模型", "") or "")
        if not dim_code:
            return {"ok": False, "message": "汇总表未配置维度模型，无法应用迟到维度更正"}
        strategy = str(payload.get("策略", "") or "迟到接管")
        if strategy not in ("迟到接管", "保留原窗口"):
            return {"ok": False, "message": f"未知处理策略「{strategy}」，可选：迟到接管、保留原窗口"}
        nk = str(payload.get("自然键", "") or "").strip()
        eff_start = str(payload.get("生效日期", "") or "").strip()
        eff_end = str(payload.get("失效日期", "") or "").strip()
        if not nk or not eff_start or not eff_end:
            return {"ok": False, "message": "迟到更正缺少自然键、生效日期或失效日期"}
        history = store.dim_history(dim_code)
        siblings = [h for h in history if str(h.get("自然键")) == nk]
        new_version = {
            "自然键": nk,
            "维度键": str(payload.get("维度键") or f"{nk}#v{len(siblings) + 1}"),
            "版本": f"v{len(siblings) + 1}",
            "生效日期": eff_start,
            "失效日期": eff_end,
            "属性": payload.get("属性", {}),
            "来源": "迟到更正",
        }
        # 维度历史接管：截断与新版本重叠的既有版本，保证同一时点只命中一个版本
        for h in siblings:
            if str(h.get("失效日期")) < eff_start or str(h.get("生效日期")) > eff_end:
                continue  # 区间不重叠
            prev_day = (date.fromisoformat(eff_start) - timedelta(days=1)).isoformat()
            if prev_day < str(h.get("生效日期")):
                store.remove_dim_history_version(dim_code, h)  # 被完全接管
            else:
                h["失效日期"] = prev_day
                h["被接管"] = True
        store.add_dim_history_version(dim_code, new_version)
        # 与已发布业务窗口的重叠判断
        published = store.get_pit_results(entry_id)
        affected_windows = [w for w in published
                            if self._window_overlap(w, eff_start, eff_end)]
        handled_at = datetime.now().isoformat()
        if not affected_windows:
            record = {"策略": strategy, "自然键": nk, "维度键": new_version["维度键"],
                      "处理时间": handled_at, "处理": "无重叠，仅登记维度历史", "窗口处理": []}
            store.add_late_handling(entry_id, record)
            return {"ok": True, "message": "迟到更正与已发布业务窗口无重叠，已登记维度历史",
                    "处理结果": record}
        outcomes: list[dict[str, Any]] = []
        for window in affected_windows:
            if strategy == "迟到接管":
                regenerated = self.generate(entry_id)
                if not regenerated.get("ok"):
                    return {"ok": False,
                            "message": f"迟到接管重算窗口 {window} 失败：{regenerated.get('message')}"}
                saved = store.get_pit_results(entry_id).get(window)
                if saved is not None:
                    saved["迟到接管"] = True
                    saved["接管维度键"] = new_version["维度键"]
                    store.save_pit_result(entry_id, window, saved)
                outcomes.append({"窗口": window, "处理": "迟到接管",
                                 "新维度键": new_version["维度键"]})
            else:
                saved = published[window]
                affected_keys = sorted({
                    m["维度键"] for m in saved.get("明细映射", [])
                    if m.get("自然键") == nk and eff_start <= m.get("业务时点", "") <= eff_end
                })
                saved["保留原窗口"] = True
                saved["受影响维度键"] = affected_keys
                store.save_pit_result(entry_id, window, saved)
                outcomes.append({"窗口": window, "处理": "保留原窗口",
                                 "受影响维度键": affected_keys})
        record = {"策略": strategy, "自然键": nk, "维度键": new_version["维度键"],
                  "处理时间": handled_at, "处理": "已处理重叠窗口", "窗口处理": outcomes}
        store.add_late_handling(entry_id, record)
        return {"ok": True,
                "message": f"迟到维度更正已按「{strategy}」处理 {len(affected_windows)} 个重叠窗口",
                "处理结果": record}

    def compare_handlings(self, entry_id: int) -> dict[str, Any]:
        """比较迟到更正两种处理的先后；谁优先尚无业务结论，当前按处理时间后者优先。"""
        entry = store.find(MODULE, entry_id)
        if not entry:
            return {"ok": False, "message": "汇总表不存在"}
        handlings = sorted(store.get_late_handlings(entry_id),
                           key=lambda h: str(h.get("处理时间", "")))
        ordered = [{"序号": index + 1,
                    "策略": h.get("策略", ""),
                    "自然键": h.get("自然键", ""),
                    "维度键": h.get("维度键", ""),
                    "处理时间": h.get("处理时间", ""),
                    "窗口处理": h.get("窗口处理", [])}
                   for index, h in enumerate(handlings)]
        current = ordered[-1]["策略"] if ordered else None
        return {"ok": True,
                "处理先后": ordered,
                "当前生效": current,
                "优先级结论": "迟到接管与保留原窗口谁优先尚无业务结论；当前按处理时间后者优先"}

    # ---- 内部工具 ----

    def _load_dwd_rows(self, entry: dict[str, Any]) -> tuple[str, list[dict[str, Any]], str | None]:
        dwd_code = str(entry.get("来源明细表", "") or "")
        dwd_entry = None
        for row in store.rows("dwd_layer"):
            if row.get("表编号") == dwd_code or row.get("表名称") == dwd_code:
                dwd_entry = row
                break
        if not dwd_entry:
            return "", [], f"来源明细表 {dwd_code} 不存在"
        dwd_table = str(dwd_entry.get("表名称", ""))
        data = store.table_rows(dwd_table)
        if not data:
            from app.services.dwd_layer import DwdLayerService
            DwdLayerService().build_dwd_from_ods(dwd_entry["id"])
            data = store.table_rows(dwd_table)
        return dwd_table, data, None

    def _parse_window(self, window: str) -> tuple[str, str] | None:
        for sep in ("/", "~"):
            if sep in window:
                start, end = window.split(sep, 1)
                start, end = start.strip(), end.strip()
                if start and end:
                    return start, end
        return None

    def _window_overlap(self, window: str, start: str, end: str) -> bool:
        parsed = self._parse_window(window)
        if parsed is None:
            return False
        return parsed[0] <= end and start <= parsed[1]

    def _apply_condition(self, rows: list[dict[str, Any]], condition: str) -> list[dict[str, Any]]:
        """条件标准：字段=值，逗号分隔多个；不满足条件的明细不进入口径。"""
        terms = [term.strip() for term in condition.split(",") if term.strip()]
        pairs: list[tuple[str, str]] = []
        for term in terms:
            if "=" not in term:
                continue
            field, expected = term.split("=", 1)
            pairs.append((field.strip(), expected.strip()))
        if not pairs:
            return rows
        return [row for row in rows
                if all(str(row.get(field, "")) == expected for field, expected in pairs)]

    def _to_float(self, value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    def _aggregate(self, entry: dict[str, Any], mappings: list[dict[str, Any]],
                   window: str) -> list[dict[str, Any]]:
        agg = str(entry.get("聚合函数", "SUM") or "SUM").upper()
        measure = str(entry.get("度量字段", "") or "")
        groups: dict[str, list[dict[str, Any]]] = {}
        for m in mappings:
            groups.setdefault(m["维度键"], []).append(m)
        rows: list[dict[str, Any]] = []
        for dim_key, items in groups.items():
            values = [m["度量原值"] for m in items]
            if agg == "COUNT":
                metric: float = float(len(items))
            elif agg == "AVG":
                metric = sum(values) / len(values) if values else 0.0
            elif agg == "MAX":
                metric = max(values) if values else 0.0
            elif agg == "MIN":
                metric = min(values) if values else 0.0
            else:
                metric = sum(values)
            first = items[0]
            rows.append({
                "维度键": dim_key,
                "自然键": first["自然键"],
                "维度版本": first["维度版本"],
                "生效区间": first["生效区间"],
                "汇总窗口": window,
                "度量字段": measure,
                "聚合函数": agg,
                "度量值": metric,
                "明细条数": len(items),
            })
        rows.sort(key=lambda r: r["维度键"])
        return rows

    def _threshold_violations(self, entry: dict[str, Any],
                              rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        lower_text = str(entry.get("阈值下界", "") or "").strip()
        upper_text = str(entry.get("阈值上界", "") or "").strip()
        lower = float(lower_text) if lower_text else None
        upper = float(upper_text) if upper_text else None
        violations: list[dict[str, Any]] = []
        for row in rows:
            value = float(row.get("度量值", 0) or 0)
            if lower is not None and value < lower:
                violations.append({**row, "越界原因": f"度量值 {value} 低于阈值下界 {lower}"})
            elif upper is not None and value > upper:
                violations.append({**row, "越界原因": f"度量值 {value} 高于阈值上界 {upper}"})
        return violations
