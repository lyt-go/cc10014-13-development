"""汇总层管理接口：维护汇总表，覆盖提交校验、确认上线、标记下线等动作。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services.dws_layer import DwsLayerService, DwsPitService

router = APIRouter(prefix="/api/dws_layer", tags=["汇总层管理"])

service = DwsLayerService()
pit_service = DwsPitService()

LIST_FIELDS = ["表编号", "统计粒度", "汇总维度", "度量字段", "聚合函数", "时间范围", "刷新周期", "创建批次"]
STATUSES = ["草稿", "待校验", "已上线", "已下线"]


def query_filters(request: Request) -> dict[str, str]:
    """只接受当前模块声明过的字段，忽略分页等保留参数。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in LIST_FIELDS and value.strip()
    }


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按表编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已上线、已下线"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """按页面字段、表编号关键字与状态过滤汇总层管理列表。"""
    items, total = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    """下载当前筛选条件下的 UTF-8 CSV；必须声明在 /{entry_id} 之前。"""
    items, _ = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=1, size=1000000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *LIST_FIELDS, "status"])
    for item in items:
        row = []
        for value in [item.get("id"), *(item.get(field, "") for field in LIST_FIELDS), item.get("status", "")]:
            text = "" if value is None else str(value)
            row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
        writer.writerow(row)
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="dws_layer.csv"'})


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条汇总表明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"汇总表 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: dict[str, object]) -> ActionResult:
    """登记一条汇总表，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="汇总表已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """对单条汇总表执行提交校验、确认上线、标记下线；不允许的动作会被拦下并说明原因。"""
    entry, message = service.run_action(entry_id, payload.action.strip())
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/aggregate")
def aggregate(entry_id: int) -> dict[str, Any]:
    """从关联DWD表读取数据，按维度聚合生成汇总表。"""
    result = service.aggregate_from_dwd(entry_id)
    return result


@router.get("/{entry_id}/data")
def get_table_data(entry_id: int) -> dict[str, Any]:
    """返回该DWS表的模拟数据。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"汇总表 {entry_id} 不存在")
    data = service.get_table_data(entry.get("表名称", ""))
    return {"items": data}


@router.post("/{entry_id}/pit_aggregate")
def pit_aggregate(entry_id: int) -> dict[str, Any]:
    """按业务时点关联DWD明细与维度历史，生成DWS时点汇总表。

    业务时点、维度生效区间和汇总窗口必须一致；关联错版本（同一明细命中两个
    维度键）或聚合结果越出阈值上下限时，拒绝保存。
    """
    return pit_service.generate(entry_id)


@router.get("/{entry_id}/pit_result")
def pit_result(entry_id: int) -> dict[str, Any]:
    """读取该汇总表已发布的时点汇总结果（生效维度键+统计窗口数据）。"""
    return pit_service.get_pit_result(entry_id)


@router.get("/{entry_id}/pit_verify")
def pit_verify(entry_id: int) -> dict[str, Any]:
    """逐项核对：每个明细只命中一个维度键，历史窗口与维度生效区间逐项可核对。"""
    return pit_service.verify(entry_id)


@router.post("/{entry_id}/late_dim_correction")
def late_dim_correction(entry_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """迟到维度更正：与已发布业务窗口重叠时按「迟到接管」或「保留原窗口」处理。"""
    return pit_service.late_dim_correction(entry_id, payload)


@router.get("/{entry_id}/late_dim_handlings")
def late_dim_handlings(entry_id: int) -> dict[str, Any]:
    """比较迟到更正两种处理的先后；谁优先尚无结论，当前按处理时间后者优先。"""
    return pit_service.compare_handlings(entry_id)
