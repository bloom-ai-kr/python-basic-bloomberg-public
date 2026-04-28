import streamlit as st

from utils.formatting import format_won, format_percent
from utils.navigation import render_sidebar_navigation
from utils.storage import init_session_state, apply_account_snapshot
from utils.kis_client import get_current_price, get_price_history, submit_order, get_account_snapshot
from utils.analysis import (
    normalize_ohlcv,
    compute_indicators,
    build_analysis_summary,
    build_analysis_context,
)
from utils.analysis import build_order_gap_summary
from utils.ai_assistant import ask_analysis_copilot, get_ai_runtime_status
from utils.news_client import fetch_company_news
from utils.ai_assistant import summarize_news_briefing


@st.cache_data(ttl=600, show_spinner=False)
def load_company_news(company_name: str, symbol: str, max_items: int, days: int) -> list[dict]:
    return fetch_company_news(
        company_name=company_name,
        symbol=symbol,
        max_items=max_items,
        days=days,
    )


st.set_page_config(
    page_title="Bloomberg | 종목 분석실",
    layout="wide",
    initial_sidebar_state="expanded",
)
init_session_state()
render_sidebar_navigation(active_page="analysis")

st.title("종목 분석실")

with st.sidebar:
    st.subheader("주문 패널")

    account_mode = st.selectbox(
        "계좌 모드",
        options=["demo", "real"],
        key="order_ticket_account_mode",
        format_func=lambda value: "모의투자" if value == "demo" else "실전투자",
    )
    order_symbol = st.text_input("종목코드", key="order_ticket_symbol")
    side = st.selectbox(
        "주문 구분",
        options=["buy", "sell"],
        key="order_ticket_side",
        format_func=lambda value: "매수" if value == "buy" else "매도",
    )
    order_type = st.selectbox(
        "주문 유형",
        options=["market", "limit"],
        key="order_ticket_type",
        format_func=lambda value: "시장가" if value == "market" else "지정가",
    )
    qty = st.number_input("수량", min_value=1, step=1, key="order_ticket_qty")
    # Price input / market refresh behavior
    if order_type == "market":
        refresh_columns = st.columns([3, 1], vertical_alignment="bottom")
        with refresh_columns[1]:
            if st.button("↻", key="refresh_market_price", help="현재 시장가 새로고침"):
                try:
                    if order_symbol and order_symbol.strip():
                        quote = get_current_price(order_symbol.strip())
                        st.session_state.order_ticket_market_price = quote.get("current_price", st.session_state.get("order_ticket_market_price", 0))
                        st.session_state.order_ticket_market_as_of = quote.get("as_of", st.session_state.get("order_ticket_market_as_of", "예시 시각"))
                except Exception:
                    st.warning("현재가를 가져오지 못했습니다.")

        with refresh_columns[0]:
            st.text_input(
                "가격",
                value=format_won(st.session_state.order_ticket_market_price),
                disabled=True,
            )

        st.caption(f"기준 시각: {st.session_state.order_ticket_market_as_of}")
        price = st.session_state.order_ticket_market_price
    else:
        st.caption(f"현재가 기준 빠른 선택: {format_won(st.session_state.order_ticket_market_price)}")
        adjust_columns = st.columns(4)
        adjust_columns[0].button("-1,000원", width="stretch", disabled=True)
        adjust_columns[1].button("-500원", width="stretch", disabled=True)
        adjust_columns[2].button("+500원", width="stretch", disabled=True)
        adjust_columns[3].button("+1,000원", width="stretch", disabled=True)

        # initialize order_ticket_price with current market price if not set
        default_price = st.session_state.get("order_ticket_price", st.session_state.get("order_ticket_market_price", 0))
        price = st.number_input(
            "가격",
            min_value=0,
            step=500,
            value=default_price,
            key="order_ticket_price",
            help="지정가 주문 시 입력하세요. 기본값은 현재가입니다.",
        )

    preview_clicked = st.button("주문 미리보기", width="stretch")
    if preview_clicked:
        symbol_for_price = order_symbol.strip() or st.session_state.get("selected_symbol", "")
        # Try to fetch latest market price for accurate comparison
        try:
            latest = get_current_price(symbol_for_price) if symbol_for_price else None
            current_price = latest.get("current_price") if latest else st.session_state.get("order_ticket_market_price", 0)
        except Exception:
            current_price = st.session_state.get("order_ticket_market_price", 0)

        order_price = price if order_type == "limit" else current_price
        total_amount = int(order_price) * int(qty)

        gap = build_order_gap_summary(
            current_price=float(current_price or 0),
            side=st.session_state.get("order_ticket_side", "buy"),
            order_type="market" if order_type == "market" else "limit",
            qty=int(qty),
            price=float(order_price),
        )

        st.subheader("주문 미리보기")
        st.write(f"종목: {symbol_for_price}")
        st.write(f"주문 유형: {'시장가' if order_type=='market' else '지정가'}")
        st.write(f"주문 수량: {qty}")
        st.write(f"주문 가격: {format_won(order_price)}")
        st.write(f"예상 주문금액: {format_won(total_amount)}")
        st.write(f"현재가: {format_won(current_price)} (기준 시각: {st.session_state.get('order_ticket_market_as_of','-')})")
        st.write(f"괴리: {format_won(gap.get('gap_price'))} ({format_percent(gap.get('gap_pct_vs_current'))})")
        if gap.get("warning"):
            st.warning(gap.get("warning"))

    # Order execution
    execute_clicked = st.button("주문 실행", type="primary")
    if execute_clicked:
        symbol_for_order = order_symbol.strip() or st.session_state.get("selected_symbol", "")
        if not symbol_for_order:
            st.error("주문할 종목코드를 입력하세요.")
        elif order_type == "limit" and float(price or 0) <= 0:
            st.error("지정가 주문은 0원보다 큰 가격을 입력하세요.")
        else:
            order_price = price if order_type == "limit" else None
            with st.spinner("주문 요청 중..."):
                try:
                    result = submit_order(
                        account_mode=account_mode,
                        symbol=symbol_for_order,
                        side=st.session_state.get("order_ticket_side", "buy"),
                        order_type=order_type,
                        qty=int(st.session_state.get("order_ticket_qty", 1)),
                        price=float(order_price) if order_price is not None else None,
                    )
                except Exception as exc:
                    result = {"status": "failed", "message": str(exc)}

            st.session_state.order_result = result
            st.subheader("주문 결과")
            if result.get("status") == "failed":
                st.error(result.get("message") or "주문 요청에 실패했습니다.")
            else:
                st.success(result.get("message") or "주문 요청이 접수되었습니다.")

            if result.get("order_id"):
                st.caption(
                    f"주문번호: {result.get('order_id')} / 주문시각: {result.get('order_time') or '-'}"
                )

            try:
                snapshot = get_account_snapshot(account_mode=account_mode)
                apply_account_snapshot(snapshot)
                st.caption(
                    f"주문/보유현황을 새로고쳤습니다. 기준 시각: {st.session_state.get('account_last_synced_at') or '-'}"
                )
            except Exception as exc:
                st.session_state.account_sync_error = str(exc)
                st.warning(f"주문 후 계좌 상태를 새로고치지 못했습니다: {exc}")

    st.info(
        "모의투자 모드에서는 KIS 모의투자 주문을 실제로 요청합니다. 주문 후 주문/보유현황 페이지에서 상태를 다시 확인할 수 있습니다."
    )

st.write(
    "종목 분석, AI 질문, 뉴스 브리핑, 주문 실행을 한 화면에서 확인하는 메인 분석 화면입니다. "
    "좌측에서 분석 기준을 저장하고, 우측과 사이드바에서 AI 응답과 주문 흐름을 이어서 확인할 수 있습니다."
)

left_col, right_col = st.columns([2, 1])

with left_col:
    st.subheader("분석 입력")
    st.info("종목코드, 시작일, 종료일을 입력한 뒤 '저장'을 눌러 분석 기준을 적용하세요.")

    with st.form("analysis_input_form"):
        symbol_input = st.text_input(
            "종목코드",
            value=st.session_state.get("selected_symbol", ""),
            help="분석할 종목 코드 (예: 005930)",
        )
        start_input = st.date_input(
            "시작일",
            value=st.session_state.get("start_date"),
        )
        end_input = st.date_input(
            "종료일",
            value=st.session_state.get("end_date"),
        )

        save_clicked = st.form_submit_button("저장")

    if save_clicked:
        if start_input > end_input:
            st.error("시작일은 종료일보다 같거나 이전이어야 합니다.")
        else:
            st.session_state.selected_symbol = symbol_input.strip() or st.session_state.get("selected_symbol", "")
            st.session_state.start_date = start_input
            st.session_state.end_date = end_input
            # 저장 직후 현재가와 기간 시세를 조회하여 지표와 요약을 계산함
            symbol = st.session_state.selected_symbol
            with st.spinner("데이터를 조회하는 중입니다..."):
                try:
                    current_quote = get_current_price(symbol)
                except Exception as exc:  # pragma: no cover - 네트워크/환경 오류 안내
                    st.error(f"현재가 조회 실패: {exc}")
                    current_quote = None

                price_records = []
                if current_quote is not None:
                    try:
                        price_records = get_price_history(symbol, st.session_state.start_date, st.session_state.end_date)
                    except Exception as exc:  # pragma: no cover - 네트워크/환경 오류 안내
                        st.error(f"기간 시세 조회 실패: {exc}")
                        price_records = []

            if not price_records:
                st.info("기간 시세 데이터가 없습니다. 다른 기간 또는 종목을 시도하세요.")
                st.session_state.price_df = None
                st.session_state.analysis_summary = None
                st.session_state.analysis_context = None
            else:
                df = normalize_ohlcv(price_records)
                df = compute_indicators(df)
                summary = build_analysis_summary(
                    symbol=symbol,
                    company_name=current_quote.get("company_name", symbol) if current_quote else symbol,
                    price_df=df,
                    current_quote=current_quote or {},
                )
                context = build_analysis_context(summary)

                st.session_state.price_df = df
                st.session_state.analysis_summary = summary
                st.session_state.analysis_context = context
                st.success("분석 기준이 저장되고 데이터가 로드되었습니다.")

    st.subheader("차트와 지표")
    if st.session_state.get("price_df") is None or st.session_state.get("analysis_summary") is None:
        st.info("분석 기준을 저장한 뒤 데이터를 불러오면 현재가, 기간 시세와 기본 지표를 표시합니다.")
    else:
        summary = st.session_state.analysis_summary
        df = st.session_state.price_df.copy()

        card_col1, card_col2, card_col3 = st.columns(3)
        card_col1.metric("현재가", format_won(summary.get("latest_close", 0)), delta=f"{format_percent(summary.get('period_return_pct',0))}")
        card_col2.metric("기간 수익률", format_percent(summary.get("period_return_pct", 0)))
        card_col3.metric("변동성(%)", format_percent(summary.get("risk", {}).get("volatility_pct", 0)))

        st.subheader("종가와 이동평균")
        try:
            plot_df = df.set_index("date")[ [col for col in ["close", "ma5", "ma20"] if col in df.columns] ]
            st.line_chart(plot_df)
        except Exception:
            st.write(df.head())

        st.subheader("기간 시세 (최근 행)")
        st.dataframe(df.tail(10))

with right_col:
    st.subheader("AI Copilot")
    # Show runtime status (OpenAI key configured?)
    runtime = get_ai_runtime_status()
    if not runtime.get("api_key_configured"):
        st.caption("OpenAI API 키가 설정되어 있지 않습니다. 폴백 메시지가 표시됩니다.")

    st.write("질문을 입력하면 현재 화면에 이미 계산된 결과를 기반으로 답변합니다.")

    if st.session_state.get("analysis_summary") is None or st.session_state.get("price_df") is None:
        st.info("먼저 좌측에서 분석 기준을 저장하여 데이터를 불러오세요. 이후 질문할 수 있습니다.")
    else:
        with st.form("copilot_form"):
            question = st.text_area("질문 입력", height=100, placeholder="예: 최근 추세가 어떻게 보이나요? 수익률과 이동평균을 참고해서 답해주세요.")
            ask_clicked = st.form_submit_button("질문하기")

        if ask_clicked and question and question.strip():
            summary = st.session_state.analysis_summary
            context = {
                "analysis_summary": summary,
                "analysis_context": st.session_state.get("analysis_context") or {},
            }
            with st.spinner("AI 응답을 요청하는 중..."):
                try:
                    resp = ask_analysis_copilot(context, question)
                except Exception:
                    resp = {"answer": "현재 서비스가 원활하지 않습니다. 나중에 다시 시도해주세요."}

            st.subheader("AI 응답")
            st.markdown(resp.get("answer", "현재 서비스가 원활하지 않습니다. 나중에 다시 시도해주세요."))

    st.subheader("뉴스 브리핑")
    summary = st.session_state.get("analysis_summary") or {}
    symbol = st.session_state.get("selected_symbol", "").strip()
    company_name = (summary.get("company_name") or symbol or "").strip()

    if not company_name:
        st.info("먼저 좌측에서 분석 기준을 저장하면 해당 종목의 연합뉴스 경제 기사를 불러옵니다.")
    else:
        st.caption("출처: 연합뉴스 최신기사 RSS, 연합뉴스 경제 RSS")

        try:
            with st.spinner("연관 기사를 불러오는 중..."):
                articles = load_company_news(company_name, symbol, max_items=5, days=7)
            news_error = ""
        except Exception as exc:
            articles = []
            news_error = str(exc)

        st.write(f"최근 {len(articles)}개 기사 — {company_name}")

        if news_error:
            st.warning(f"뉴스를 불러오지 못했습니다: {news_error}")
        elif not articles:
            st.info(f"최근 7일 내 연합뉴스 최신기사 RSS와 경제 RSS에서 {company_name} 관련 기사를 찾지 못했습니다.")

        for art in articles:
            title = art.get("title") or "제목 없음"
            link = art.get("link") or ""
            source = art.get("source_name") or art.get("source_url") or "출처 없음"
            published = art.get("published_at") or "발행 시각 없음"

            cols = st.columns([8, 2])
            with cols[0]:
                if link:
                    st.markdown(f"[{title}]({link})")
                else:
                    st.write(title)
                if art.get("description"):
                    st.caption(art.get("description"))
            with cols[1]:
                st.write(source)
                st.write(published)

        if articles:
            briefing = summarize_news_briefing(company_name, articles)
            st.subheader("뉴스 브리핑 요약")
            st.write(briefing.get("summary") or "요약을 생성할 수 없습니다.")
            if briefing.get("opportunities"):
                st.markdown("**기회:** " + ", ".join(briefing.get("opportunities")))
            if briefing.get("warnings"):
                st.markdown("**경고:** " + ", ".join(briefing.get("warnings")))
            if briefing.get("needs_confirmation"):
                st.markdown("**확인 필요:** " + ", ".join(briefing.get("needs_confirmation")))
