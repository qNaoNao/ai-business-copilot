"""API-free governed analysis workbench, shared with the chat's metric tool."""
import json
import sqlite3

import altair as alt
import pandas as pd
import streamlit as st

from src.analysis_store import AnalysisStore
from src.export_safety import safe_csv
from src.semantic import CATALOG_VERSION, DIMENSIONS, METRICS, catalog, compile_query


def render_result(result, *, snapshot=False):
    st.caption(("历史快照 · 未执行新查询" if snapshot else "真实只读 SQL 查询 · 不调用模型 API")
               + f" · {result['row_count']} 行 · {result['latency_ms']:.1f} ms（原执行耗时）")
    if result["truncated"]:
        st.warning("结果超过行数上限，当前仅展示截断结果，不代表全部分组。")
    frame = pd.DataFrame(result["rows"], columns=result["spec"]["dimensions"] + result["spec"]["metrics"])
    st.dataframe(frame, hide_index=True, width="stretch")
    dims, metrics = result["spec"]["dimensions"], result["spec"]["metrics"]
    if not frame.empty and dims:
        metric = st.selectbox("图表指标", metrics, key="wb_chart_metric")
        x = dims[0]
        chart = alt.Chart(frame).mark_bar().encode(
            x=alt.X(f"{x}:N", sort=None), y=alt.Y(f"{metric}:Q"),
            tooltip=[alt.Tooltip(f"{d}:N") for d in dims] + [alt.Tooltip(f"{metric}:Q")])
        if len(dims) == 2:
            chart = chart.encode(color=alt.Color(f"{dims[1]}:N"), xOffset=alt.XOffset(f"{dims[1]}:N"))
        st.altair_chart(chart.properties(height=280), width="stretch")
    with st.expander("SQL、口径与查询血缘"):
        st.code(result["sql"], language="sql")
        st.json({key: result.get(key) for key in ("parameters", "definitions", "query_id", "catalog_version", "source_version", "source_version_kind", "source_contract", "query_plan")}, expanded=False)
        for note in result["scope_notes"]:
            st.caption(note)
    left, right = st.columns(2)
    left.download_button("下载查询证据 JSON", json.dumps(result, ensure_ascii=False, indent=2),
                         file_name="analysis-evidence.json", mime="application/json", key="wb_json")
    right.download_button("下载查询数据 CSV", safe_csv(frame),
                          file_name="analysis-data.csv", mime="text/csv", key="wb_csv")


def render_workbench():
    try:
        _render_workbench()
    except (OSError, sqlite3.Error, ValueError, KeyError):
        st.error("分析资产暂不可读写，请检查存储权限或快照完整性；请勿重复执行付费分析。")


def _render_workbench():
    st.header("受控分析工作台")
    st.caption("自行组合指标与维度 → 预览 SQL → 执行 → 保存 → 回放/复核。全过程不需要 API Key。")
    try:
        store = AnalysisStore()
    except OSError:
        st.error("分析库不可写，请检查 runs 目录权限。聊天功能仍可单独使用。")
        return
    with st.expander("指标目录与能力边界"):
        st.caption(f"口径版本 {CATALOG_VERSION} · 最多2个维度 / 500行 / 1096天 / 5秒执行预算")
        st.dataframe(pd.DataFrame(catalog()["metrics"]), hide_index=True, width="stretch")
        for note in catalog()["scope_notes"]:
            st.caption(note)
    with st.form("wb_query"):
        dates = st.columns(2)
        start = dates[0].text_input("开始日期", "2018-01-01", key="wb_start")
        end = dates[1].text_input("结束日期", "2018-02-28", key="wb_end")
        metrics = st.multiselect("选择指标", list(METRICS), default=["gmv", "orders", "customers", "aov"], key="wb_metrics")
        dimensions = st.multiselect("分析维度（最多2个）", list(DIMENSIONS), default=["month", "category"], key="wb_dimensions")
        filters = st.columns(2)
        category = filters[0].text_input("品类筛选（留空表示全部）", key="wb_category")
        product = filters[1].text_input("product_id筛选（留空表示全部）", key="wb_product")
        limit = st.number_input("最多返回行数", min_value=1, max_value=500, value=100, key="wb_limit")
        preview = st.form_submit_button("预览查询")
        execute = st.form_submit_button("执行受控查询", type="primary")
    if preview or execute:
        spec = dict(metrics=metrics, dimensions=dimensions, start_date=start, end_date=end,
                    category=category or None, product_id=product or None, limit=int(limit))
        try:
            plan = compile_query(spec)
            st.session_state.wb_preview = {"sql": plan.sql, "parameters": plan.parameters}
            if execute:
                st.session_state.wb_result = store.run(spec)
                st.session_state.wb_is_snapshot = False
                st.session_state.wb_loaded_id = None
        except Exception as exc:
            st.error(f"查询未完成：{exc}")
            st.session_state.pop("wb_result", None)
    if st.session_state.get("wb_preview"):
        with st.expander("最近预览的参数化 SQL", expanded=preview):
            st.code(st.session_state.wb_preview["sql"], language="sql")
            st.write(st.session_state.wb_preview["parameters"])
    result = st.session_state.get("wb_result")
    if result:
        render_result(result, snapshot=st.session_state.get("wb_is_snapshot", False))
        with st.form("wb_save"):
            title = st.text_input("分析名称", "月度品类经营分析", key="wb_title")
            if st.form_submit_button("保存分析快照"):
                try:
                    identifier = store.save(title, result)
                    st.session_state.wb_loaded_id = identifier
                    st.success("已保存查询条件、结果、SQL和版本信息。")
                except (OSError, ValueError) as exc:
                    st.error(f"未能保存：{exc}")
    st.subheader("分析资产库")
    records = store.list()
    if records:
        selected = st.selectbox("选择已保存分析", records, format_func=lambda r: f"{r['title']} · {r['created_at'][:19]}", key="wb_saved")
        left, right = st.columns(2)
        if left.button("查看快照（不重新查询）", key="wb_snapshot"):
            st.session_state.wb_result = store.get(selected["id"])["result"]
            st.session_state.wb_is_snapshot = True
            st.session_state.wb_loaded_id = selected["id"]
            st.rerun()
        if right.button("用当前数据重新执行", key="wb_replay"):
            try:
                replay = store.replay(selected["id"])
                st.session_state.wb_result = replay["result"]
                st.session_state.wb_is_snapshot = False
                st.session_state.wb_replay_comparison = {k: v for k, v in replay.items() if k != "result"}
                st.rerun()
            except Exception as exc:
                st.error(f"重跑未完成：{exc}")
        if st.session_state.get("wb_replay_comparison"):
            st.write("最近重跑与原快照比较", st.session_state.wb_replay_comparison)
        with st.form("wb_review"):
            verdict = st.radio("人工复核（不是模型准确率）", ["needs_review", "verified"], key="wb_verdict")
            note = st.text_input("复核备注", key="wb_note")
            if st.form_submit_button("记录复核意见"):
                try:
                    store.review(selected["id"], verdict, note)
                    st.success("复核意见已记录。")
                except ValueError as exc:
                    st.error(str(exc))
        st.json(store.get(selected["id"])["feedback"], expanded=False)
    else:
        st.caption("还没有保存的分析；查询后可以保存为可复用资产。")
    with st.expander("查询运行监控（本地）"):
        st.json(store.stats(), expanded=False)
