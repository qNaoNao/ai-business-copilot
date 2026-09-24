"""Local Streamlit chat interface for the AI Business Copilot."""

from datetime import datetime
import os
from typing import Any
from uuid import uuid4

import altair as alt
from dotenv import load_dotenv
import pandas as pd
import streamlit as st

from src.agent import (
    DEFAULT_MODEL,
    PROJECT_ROOT,
    create_client,
    run_agent_turn,
)
from src.report_export import build_analysis_report
from src.run_history import (
    group_history_sessions,
    history_records_to_messages,
    load_run_history,
    save_run_safely,
)
from src.ui_helpers import build_csv_downloads
from src.ui_visualizations import build_tool_visualizations


def reset_conversation() -> None:
    """Start a new local and OpenAI conversation chain."""

    st.session_state.messages = []
    st.session_state.session_id = uuid4().hex
    st.session_state.previous_response_id = None
    st.session_state.turn_number = 1
    st.session_state.viewing_history_session_id = None
    st.session_state.history_messages = []


def initialize_state() -> None:
    """Create the values that must survive Streamlit reruns."""

    if "messages" not in st.session_state:
        reset_conversation()

    st.session_state.setdefault(
        "viewing_history_session_id",
        None,
    )
    st.session_state.setdefault("history_messages", [])


def format_history_session_label(
    session: dict[str, Any],
) -> str:
    """Build a short, local-time label for the history picker."""

    timestamp = session.get("latest_timestamp", "")
    try:
        local_datetime = datetime.fromisoformat(timestamp).astimezone()
        date_label = local_datetime.strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        date_label = "时间未知"

    preview = " ".join(
        str(session.get("preview", "未命名记录")).split()
    )
    if len(preview) > 28:
        preview = f"{preview[:28]}…"

    return (
        f"{date_label} · {session['turn_count']}轮 · {preview}"
    )


def render_tool_details(
    tool_trace: list[dict[str, Any]],
    *,
    turn_number: int,
) -> None:
    """Show tool calls and make successful table results downloadable."""

    if not tool_trace:
        return

    downloads_by_call = {
        item["call_number"]: item
        for item in build_csv_downloads(
            tool_trace,
            turn_number=turn_number,
        )
    }

    with st.expander(
        f"查看本轮使用的数据工具（{len(tool_trace)}）",
        expanded=False,
    ):
        for call_number, tool_call in enumerate(tool_trace, start=1):
            tool_name = tool_call.get("name", "unknown_tool")
            output = tool_call.get("output", {})

            st.markdown(f"**{call_number}. `{tool_name}`**")
            st.caption("查询条件")
            st.json(tool_call.get("arguments", {}), expanded=False)

            if output.get("ok"):
                rows = output.get("data", [])
                if isinstance(rows, list) and rows:
                    st.dataframe(
                        pd.DataFrame(rows),
                        width="stretch",
                        hide_index=True,
                    )
                elif isinstance(rows, list):
                    st.info("这个查询没有返回数据行。")
                else:
                    st.json(rows, expanded=False)
            else:
                st.error(output.get("error", "工具执行失败。"))

            download = downloads_by_call.get(call_number)
            if download:
                st.download_button(
                    label=(
                        f"下载 CSV（{download['row_count']} 行）"
                    ),
                    data=download["data"],
                    file_name=download["file_name"],
                    mime="text/csv",
                    key=(
                        f"download_{turn_number}_{call_number}_"
                        f"{tool_name}"
                    ),
                )

            if call_number < len(tool_trace):
                st.divider()


def render_metric_cards(cards: list[dict[str, Any]]) -> None:
    """Render a responsive row of prepared KPI cards."""

    if not cards:
        return

    columns = st.columns(len(cards))
    for column, card in zip(columns, cards):
        column.metric(
            label=card["label"],
            value=card["value"],
            delta=card.get("delta"),
        )


def render_contribution_chart(
    visualization: dict[str, Any],
) -> None:
    """Render category or SKU daily-GMV contributions."""

    frame = pd.DataFrame(visualization["rows"])
    tooltip = [
        alt.Tooltip("full_label:N", title=(
            "品类"
            if visualization["entity"] == "category"
            else "product_id"
        )),
        alt.Tooltip(
            "base_daily_gmv:Q",
            title="基期日均 GMV",
            format=",.2f",
        ),
        alt.Tooltip(
            "compare_daily_gmv:Q",
            title="比较期日均 GMV",
            format=",.2f",
        ),
        alt.Tooltip(
            "daily_gmv_change:Q",
            title="日均 GMV 变化",
            format=",.2f",
        ),
        alt.Tooltip(
            "daily_change_pct:Q",
            title="变化百分比",
            format=".2f",
        ),
    ]
    if visualization["entity"] == "sku":
        tooltip.append(
            alt.Tooltip("status:N", title="销售状态")
        )

    chart = (
        alt.Chart(frame)
        .mark_bar()
        .encode(
            x=alt.X(
                "daily_gmv_change:Q",
                title="日均 GMV 变化",
            ),
            y=alt.Y(
                "label:N",
                title=None,
                sort=alt.SortField(
                    field="daily_gmv_change",
                    order="ascending",
                ),
            ),
            tooltip=tooltip,
        )
        .properties(
            height=max(150, min(420, len(frame) * 34)),
        )
    )
    st.altair_chart(chart, width="stretch")


def render_monthly_chart(
    visualization: dict[str, Any],
) -> None:
    """Render daily-normalized GMV for monthly comparisons."""

    frame = pd.DataFrame(visualization["rows"])
    chart = (
        alt.Chart(frame)
        .mark_line(point=True)
        .encode(
            x=alt.X("month:N", title="月份"),
            y=alt.Y(
                "daily_gmv:Q",
                title="日均 GMV",
                scale=alt.Scale(zero=False),
            ),
            tooltip=[
                alt.Tooltip("month:N", title="月份"),
                alt.Tooltip(
                    "daily_gmv:Q",
                    title="日均 GMV",
                    format=",.2f",
                ),
                alt.Tooltip(
                    "gmv:Q",
                    title="月度 GMV",
                    format=",.2f",
                ),
                alt.Tooltip(
                    "days_in_month:Q",
                    title="自然日数",
                    format=".0f",
                ),
            ],
        )
        .properties(height=240)
    )
    st.altair_chart(chart, width="stretch")


def render_time_trend(
    visualization: dict[str, Any],
) -> None:
    """Render GMV and volume trends on separate, readable scales."""

    frame = pd.DataFrame(visualization["rows"])
    frame["period_start"] = pd.to_datetime(frame["period_start"])

    gmv_chart = (
        alt.Chart(frame)
        .mark_line(point=True)
        .encode(
            x=alt.X("period_start:T", title="期间开始日期"),
            y=alt.Y("gmv:Q", title="GMV", scale=alt.Scale(zero=True)),
            tooltip=[
                alt.Tooltip(
                    "period_start:T",
                    title="开始日期",
                    format="%Y-%m-%d",
                ),
                alt.Tooltip(
                    "period_end:N",
                    title="结束日期",
                ),
                alt.Tooltip("gmv:Q", title="GMV", format=",.2f"),
                alt.Tooltip(
                    "is_partial_period:N",
                    title="是否部分期间",
                ),
            ],
        )
        .properties(title="GMV 趋势", height=220)
    )

    volume_frame = frame.melt(
        id_vars=["period_start", "period_end"],
        value_vars=["orders", "items"],
        var_name="metric",
        value_name="value",
    )
    volume_frame["metric"] = volume_frame["metric"].map({
        "orders": "订单数",
        "items": "件数",
    })
    volume_chart = (
        alt.Chart(volume_frame)
        .mark_line(point=True)
        .encode(
            x=alt.X("period_start:T", title="期间开始日期"),
            y=alt.Y("value:Q", title="数量", scale=alt.Scale(zero=True)),
            color=alt.Color("metric:N", title=None),
            tooltip=[
                alt.Tooltip(
                    "period_start:T",
                    title="开始日期",
                    format="%Y-%m-%d",
                ),
                alt.Tooltip("metric:N", title="指标"),
                alt.Tooltip("value:Q", title="数值", format=",.0f"),
            ],
        )
        .properties(title="订单数与件数趋势", height=220)
    )

    st.altair_chart(gmv_chart, width="stretch")
    st.altair_chart(volume_chart, width="stretch")


def render_visualizations(tool_trace: list[dict[str, Any]]) -> None:
    """Render all supported charts produced by one conversation turn."""

    visualizations = build_tool_visualizations(tool_trace)
    if not visualizations:
        return

    st.markdown("##### 图表与指标")

    for index, visualization in enumerate(visualizations):
        if index:
            st.divider()

        st.markdown(f"**{visualization['title']}**")
        kind = visualization["kind"]

        if kind in {"monthly_kpis", "category_comparison"}:
            render_metric_cards(visualization["cards"])
        if kind == "monthly_kpis":
            render_monthly_chart(visualization)
        elif kind == "contribution":
            render_contribution_chart(visualization)
        elif kind == "time_trend":
            render_time_trend(visualization)


def render_message(message: dict[str, Any]) -> None:
    """Render one saved browser-session message."""

    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant":
            render_visualizations(
                message.get("tool_trace", []),
            )
            render_tool_details(
                message.get("tool_trace", []),
                turn_number=message.get("turn_number", 0),
            )


def render_report_downloads(
    messages: list[dict[str, Any]],
    *,
    session_id: str,
    report_model: str,
) -> None:
    """Offer one-click Markdown and HTML reports for a visible session."""

    if not messages:
        return

    report = build_analysis_report(
        messages,
        session_id=session_id,
        model=report_model,
    )

    with st.expander("导出本次会话报告", expanded=False):
        st.caption(
            "Markdown 适合继续编辑；HTML 适合浏览器查看和打印。"
            "生成报告不会调用 OpenAI API。"
        )
        markdown_column, html_column = st.columns(2)
        markdown_column.download_button(
            "下载 Markdown 报告",
            data=report.markdown.encode("utf-8"),
            file_name=report.markdown_file_name,
            mime="text/markdown",
            width="stretch",
            key=f"report_md_{session_id}",
        )
        html_column.download_button(
            "下载 HTML 报告",
            data=report.html.encode("utf-8"),
            file_name=report.html_file_name,
            mime="text/html",
            width="stretch",
            key=f"report_html_{session_id}",
        )


st.set_page_config(
    page_title="AI Business Copilot",
    page_icon="📊",
    layout="wide",
)

load_dotenv(PROJECT_ROOT / ".env")
initialize_state()
model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
history_sessions = group_history_sessions(load_run_history())
history_by_id = {
    session["session_id"]: session
    for session in history_sessions
}

st.title("📊 AI Business Copilot")
st.caption("用自然语言查询经营数据，并连续追问品类、SKU 和时间趋势。")

with st.sidebar:
    if st.session_state.viewing_history_session_id:
        st.subheader("历史回溯")
        st.caption("当前正在只读查看已保存的会话。")
        if st.button("返回当前会话", width="stretch"):
            st.session_state.viewing_history_session_id = None
            st.session_state.history_messages = []
            st.rerun()
    else:
        st.subheader("当前会话")
        st.caption(f"模型：{model}")
        st.caption(
            "会话编号："
            f"{st.session_state.session_id[:8]}"
        )
        st.info(
            "每次提交问题都会调用 OpenAI API 并产生少量费用。"
            "工具查询结果来自本地数据库。"
        )

    if st.button(
        "开始新会话",
        width="stretch",
        type="secondary",
    ):
        reset_conversation()
        st.rerun()

    st.divider()
    st.subheader("历史会话")

    if history_sessions:
        session_ids = [
            session["session_id"]
            for session in history_sessions
        ]
        selected_history_id = st.selectbox(
            "选择记录",
            options=session_ids,
            format_func=lambda session_id: (
                format_history_session_label(
                    history_by_id[session_id]
                )
            ),
        )
        if st.button(
            "查看选中的历史",
            width="stretch",
            type="secondary",
        ):
            selected_session = history_by_id[selected_history_id]
            st.session_state.history_messages = (
                history_records_to_messages(
                    selected_session["records"]
                )
            )
            st.session_state.viewing_history_session_id = (
                selected_history_id
            )
            st.rerun()
    else:
        st.caption("还没有保存的历史记录。")

viewing_history = bool(
    st.session_state.viewing_history_session_id
)
messages_to_render = (
    st.session_state.history_messages
    if viewing_history
    else st.session_state.messages
)

if viewing_history:
    st.info(
        "这是已保存的只读历史，不会调用 OpenAI API。"
        "返回当前会话后可以继续提问。"
    )

visible_session_id = (
    str(st.session_state.viewing_history_session_id)
    if viewing_history
    else st.session_state.session_id
)
visible_model = model
if viewing_history:
    visible_history_session = history_by_id.get(visible_session_id, {})
    visible_model = ", ".join(
        visible_history_session.get("models", [])
    ) or "未记录"

render_report_downloads(
    messages_to_render,
    session_id=visible_session_id,
    report_model=visible_model,
)

for saved_message in messages_to_render:
    render_message(saved_message)

question = (
    None
    if viewing_history
    else st.chat_input("请输入经营分析问题……")
)

if question:
    turn_number = st.session_state.turn_number
    previous_response_id = (
        st.session_state.previous_response_id
    )
    user_message = {
        "role": "user",
        "content": question,
    }
    st.session_state.messages.append(user_message)
    render_message(user_message)

    tool_trace: list[dict[str, Any]] = []

    with st.chat_message("assistant"):
        try:
            with st.spinner("正在查询数据并分析……"):
                result = run_agent_turn(
                    question,
                    client=create_client(),
                    model=model,
                    tool_trace=tool_trace,
                    previous_response_id=previous_response_id,
                )
        except Exception as error:
            error_text = f"这轮分析没有完成：{error}"
            _, history_error = save_run_safely(
                question=question,
                answer=None,
                tool_trace=tool_trace,
                model=model,
                error=str(error),
                session_id=st.session_state.session_id,
                turn_number=turn_number,
                previous_response_id=previous_response_id,
            )
            st.error(error_text)
            if history_error:
                st.warning(
                    "错误详情也未能保存到本地历史："
                    f"{history_error}"
                )
            st.session_state.messages.append({
                "role": "assistant",
                "content": error_text,
                "tool_trace": tool_trace,
                "turn_number": turn_number,
            })
            st.session_state.turn_number += 1
            render_visualizations(tool_trace)
            render_tool_details(
                tool_trace,
                turn_number=turn_number,
            )
        else:
            _, history_error = save_run_safely(
                question=question,
                answer=result.answer,
                tool_trace=tool_trace,
                model=model,
                session_id=st.session_state.session_id,
                turn_number=turn_number,
                previous_response_id=previous_response_id,
                response_id=result.response_id,
            )
            assistant_message = {
                "role": "assistant",
                "content": result.answer,
                "tool_trace": tool_trace,
                "turn_number": turn_number,
            }
            st.session_state.messages.append(assistant_message)
            st.session_state.previous_response_id = result.response_id
            st.session_state.turn_number += 1

            st.markdown(result.answer)
            if history_error:
                st.warning(
                    "回答已经完成，但未能保存到本地历史："
                    f"{history_error}"
                )
            render_visualizations(tool_trace)
            render_tool_details(
                tool_trace,
                turn_number=turn_number,
            )
