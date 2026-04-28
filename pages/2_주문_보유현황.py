import streamlit as st

from utils.formatting import format_number, format_percent, format_won
from utils.kis_client import get_account_snapshot
from utils.navigation import render_sidebar_navigation
from utils.storage import apply_account_snapshot, init_session_state


def refresh_account_state(show_status: bool = True) -> None:
    account_mode = st.session_state.get("order_ticket_account_mode", "demo")
    try:
        snapshot = get_account_snapshot(account_mode=account_mode)
        apply_account_snapshot(snapshot)
        if show_status:
            st.success("주문/보유현황을 새로고쳤습니다.")
    except Exception as exc:
        st.session_state.account_sync_error = str(exc)
        if show_status:
            st.error(f"계좌 상태를 불러오지 못했습니다: {exc}")


def build_order_table(rows: list[dict]) -> list[dict]:
    table: list[dict] = []
    for row in rows:
        table.append(
            {
                "주문시각": row.get("ordered_at") or "-",
                "주문번호": row.get("order_id") or "-",
                "종목코드": row.get("symbol") or "-",
                "종목명": row.get("company_name") or "-",
                "구분": row.get("side_label") or "-",
                "주문유형": row.get("order_type_label") or "-",
                "주문수량": format_number(row.get("qty")),
                "주문단가": format_won(row.get("price")),
                "체결수량": format_number(row.get("filled_qty")),
                "미체결수량": format_number(row.get("remaining_qty")),
                "상태": row.get("status_label") or row.get("status") or "-",
            }
        )
    return table


def build_holding_table(rows: list[dict]) -> list[dict]:
    table: list[dict] = []
    for row in rows:
        table.append(
            {
                "종목코드": row.get("symbol") or "-",
                "종목명": row.get("company_name") or "-",
                "보유수량": format_number(row.get("qty")),
                "주문가능수량": format_number(row.get("available_qty")),
                "매입평균가": format_won(row.get("avg_price")),
                "현재가": format_won(row.get("current_price")),
                "평가금액": format_won(row.get("evaluation_amount")),
                "평가손익": format_won(row.get("profit_loss")),
                "평가손익률": format_percent(row.get("profit_loss_rate")),
            }
        )
    return table


st.set_page_config(
    page_title="Bloomberg | 주문/보유현황",
    layout="wide",
    initial_sidebar_state="expanded",
)
init_session_state()
render_sidebar_navigation(active_page="operations")

if not st.session_state.get("account_last_synced_at") and not st.session_state.get("account_sync_error"):
    refresh_account_state(show_status=False)

st.title("주문/보유현황")
st.write("모의투자 또는 실전투자 계좌의 주문 상태, 미체결 주문, 보유 종목과 예수금 현황을 확인합니다.")

with st.sidebar:
    st.subheader("계좌 상태")
    st.write(f"계좌 모드: {'모의투자' if st.session_state.get('order_ticket_account_mode') == 'demo' else '실전투자'}")
    if st.button("현황 새로고침", type="primary", width="stretch"):
        refresh_account_state(show_status=True)
    if st.session_state.get("account_last_synced_at"):
        st.caption(f"최근 동기화: {st.session_state.get('account_last_synced_at')}")

if st.session_state.get("account_sync_error"):
    st.warning(f"최근 동기화 오류: {st.session_state.get('account_sync_error')}")

balance_snapshot = st.session_state.get("balance_snapshot") or {}
summary = balance_snapshot.get("summary") or {}
portfolio = st.session_state.get("portfolio_items", [])
order_items = st.session_state.get("order_items", [])
open_orders = st.session_state.get("open_order_items", [])

metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
metric_col1.metric("예수금", format_won(summary.get("cash", 0)))
metric_col2.metric("유가평가금액", format_won(summary.get("securities_value", 0)))
metric_col3.metric("총평가금액", format_won(summary.get("total_value", 0)))
metric_col4.metric("평가손익 합계", format_won(summary.get("profit_loss_total", 0)))

col1, col2 = st.columns(2)
with col1:
    st.subheader("주문 상태")
    st.write("최근 주문 내역")
    if not order_items:
        st.info("조회된 주문 내역이 없습니다.")
    else:
        st.dataframe(build_order_table(order_items), width="stretch", hide_index=True)

    st.write("미체결 주문")
    if not open_orders:
        st.info("미체결 주문이 없습니다.")
    else:
        st.dataframe(build_order_table(open_orders), width="stretch", hide_index=True)

with col2:
    st.subheader("보유 현황")
    if not portfolio:
        st.info("보유 종목이 없습니다.")
    else:
        st.dataframe(build_holding_table(portfolio), width="stretch", hide_index=True)

    st.write("예수금 및 요약")
    if not summary:
        st.info("예수금 요약이 없습니다.")
    else:
        st.dataframe(
            [
                {"항목": "예수금", "값": format_won(summary.get("cash", 0))},
                {"항목": "익일정산금액", "값": format_won(summary.get("next_day_cash", 0))},
                {"항목": "매입금액합계", "값": format_won(summary.get("purchase_amount_total", 0))},
                {"항목": "평가금액합계", "값": format_won(summary.get("evaluation_amount_total", 0))},
                {"항목": "평가손익합계", "값": format_won(summary.get("profit_loss_total", 0))},
                {"항목": "순자산금액", "값": format_won(summary.get("net_asset", 0))},
                {"항목": "자산증감률", "값": format_percent(summary.get("asset_change_rate", 0))},
            ],
            width="stretch",
            hide_index=True,
        )
