import hashlib
import json
import math
import os
import random
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover - 초기 설치 전 보호
    requests = None

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - 초기 설치 전 보호
    def load_dotenv() -> None:
        return None


load_dotenv()

REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"
PAPER_BASE_URL = "https://openapivts.koreainvestment.com:29443"
TOKEN_ENDPOINT = "/oauth2/tokenP"
HASHKEY_ENDPOINT = "/uapi/hashkey"
PRICE_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-price"
PRICE_TR_ID = "FHKST01010100"
DAILY_CHART_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
DAILY_CHART_TR_ID = "FHKST03010100"
ORDER_CASH_ENDPOINT = "/uapi/domestic-stock/v1/trading/order-cash"
BALANCE_ENDPOINT = "/uapi/domestic-stock/v1/trading/inquire-balance"
DAILY_CCLD_ENDPOINT = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
KIS_MARKET_CODE = "J"

_TOKEN_CACHE: dict[str, dict[str, Any]] = {}
_LAST_REQUEST_TS = 0.0


def _company_name(symbol: str) -> str:
    return f"{symbol} 종목" if symbol else "종목"


def _base_price(symbol: str) -> int:
    numeric = sum(ord(char) for char in symbol)
    return 50000 + (numeric % 70000)


def _missing_env() -> list[str]:
    required = [
        "KIS_APP_KEY",
        "KIS_APP_SECRET",
        "KIS_ACCOUNT_NO",
        "KIS_ACCOUNT_PRODUCT_CODE",
    ]
    return [name for name in required if not os.getenv(name, "").strip()]


def _env_name() -> str:
    return "real" if os.getenv("KIS_ENV", "demo").strip().lower() == "real" else "demo"


def _coerce_env_name(env_name: str | None = None) -> str:
    candidate = (env_name or _env_name()).strip().lower()
    return "real" if candidate == "real" else "demo"


def _base_url(env_name: str) -> str:
    return REAL_BASE_URL if env_name == "real" else PAPER_BASE_URL


def _token_cache_path(env_name: str) -> Path:
    app_key = os.getenv("KIS_APP_KEY", "").strip()
    digest = hashlib.sha256(f"{env_name}:{app_key}".encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"kis_token_cache_{env_name}_{digest}.json"


def _throttle_requests(min_interval_seconds: float = 0.35) -> None:
    global _LAST_REQUEST_TS

    now = time.monotonic()
    wait_seconds = min_interval_seconds - (now - _LAST_REQUEST_TS)
    if wait_seconds > 0:
        time.sleep(wait_seconds)
    _LAST_REQUEST_TS = time.monotonic()


def _force_sample_data() -> bool:
    value = os.getenv("KIS_FORCE_SAMPLE", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _sample_current_price(symbol: str, source: str) -> dict[str, Any]:
    base_price = _base_price(symbol)
    return {
        "stock_code": symbol,
        "company_name": _company_name(symbol),
        "current_price": base_price,
        "change_pct": 0.0,
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": source,
    }


def _sample_price_history(symbol: str, start_date: date, end_date: date) -> list[dict[str, Any]]:
    rng = random.Random(symbol)
    records: list[dict[str, Any]] = []
    base_price = float(_base_price(symbol))
    current_date = start_date
    index = 0

    while current_date <= end_date:
        if current_date.weekday() < 5:
            drift = math.sin(index / 4) * 0.01
            noise = (rng.random() - 0.5) * 0.02
            close_price = max(1000.0, base_price * (1 + drift + noise))
            open_price = max(1000.0, close_price * (1 + (rng.random() - 0.5) * 0.01))
            high_price = max(open_price, close_price) * 1.01
            low_price = min(open_price, close_price) * 0.99

            records.append(
                {
                    "date": current_date.isoformat(),
                    "open": round(open_price, 2),
                    "high": round(high_price, 2),
                    "low": round(low_price, 2),
                    "close": round(close_price, 2),
                    "volume": 1000000 + index * 5000,
                }
            )
            base_price = close_price
            index += 1
        current_date = current_date.fromordinal(current_date.toordinal() + 1)

    return records


def _read_cached_token(env_name: str) -> str | None:
    cache_file = _token_cache_path(env_name)
    if not cache_file.exists():
        return None

    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        expires_at = datetime.fromisoformat(payload["expires_at"])
        if expires_at <= datetime.now():
            return None
        access_token = str(payload.get("access_token") or "").strip()
        return access_token or None
    except Exception:
        return None


def _clear_cached_token(env_name: str) -> None:
    _TOKEN_CACHE.pop(env_name, None)
    cache_file = _token_cache_path(env_name)
    try:
        if cache_file.exists():
            cache_file.unlink()
    except Exception:
        return None


def _write_cached_token(env_name: str, access_token: str, expires_at: datetime) -> None:
    cache_file = _token_cache_path(env_name)
    try:
        cache_file.write_text(
            json.dumps(
                {
                    "access_token": access_token,
                    "expires_at": expires_at.isoformat(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        return None


def _safe_json(response: Any) -> dict[str, Any]:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _is_rate_limited(payload: dict[str, Any]) -> bool:
    msg_cd = str(payload.get("msg_cd") or "")
    message = " ".join(
        str(payload.get(key) or "")
        for key in ("msg1", "message", "error_description")
    )
    return msg_cd == "EGW00201" or "초당 거래건수를 초과" in message


def _is_token_expired(payload: dict[str, Any]) -> bool:
    message = " ".join(
        str(payload.get(key) or "")
        for key in ("msg1", "message", "error_description")
    )
    return "만료된 token" in message or "기간이 만료된 token" in message


def _get_access_token(env_name: str | None = None) -> str:
    if requests is None:
        raise RuntimeError("requests 패키지가 없어 KIS API를 호출할 수 없습니다.")

    env_name = _coerce_env_name(env_name)
    cached = _TOKEN_CACHE.get(env_name)
    if cached and cached["expires_at"] > datetime.now():
        return cached["access_token"]

    disk_cached_token = _read_cached_token(env_name)
    if disk_cached_token:
        expires_at = datetime.now() + timedelta(minutes=30)
        _TOKEN_CACHE[env_name] = {
            "access_token": disk_cached_token,
            "expires_at": expires_at,
        }
        return disk_cached_token

    app_key = os.getenv("KIS_APP_KEY", "").strip()
    app_secret = os.getenv("KIS_APP_SECRET", "").strip()
    url = f"{_base_url(env_name)}{TOKEN_ENDPOINT}"

    _throttle_requests()
    response = requests.post(
        url,
        headers={"Content-Type": "application/json"},
        json={
            "grant_type": "client_credentials",
            "appkey": app_key,
            "appsecret": app_secret,
        },
        timeout=15,
    )
    data = _safe_json(response)
    response.raise_for_status()
    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError(f"KIS 토큰 응답에 access_token이 없습니다: {data}")

    ttl_seconds = max(_to_int(data.get("expires_in"), 0), 300)
    expires_at = datetime.now() + timedelta(seconds=max(ttl_seconds - 60, 60))
    _TOKEN_CACHE[env_name] = {
        "access_token": access_token,
        "expires_at": expires_at,
    }
    _write_cached_token(env_name, access_token, expires_at)
    return access_token


def _build_kis_headers(
    *,
    tr_id: str,
    env_name: str | None = None,
    tr_cont: str = "",
    extra_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    env_name = _coerce_env_name(env_name)
    app_key = os.getenv("KIS_APP_KEY", "").strip()
    app_secret = os.getenv("KIS_APP_SECRET", "").strip()
    access_token = _get_access_token(env_name)

    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {access_token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": tr_id,
        "custtype": "P",
        "tr_cont": tr_cont,
    }
    if extra_headers:
        headers.update(extra_headers)
    return headers


def _get_hashkey(payload: dict[str, Any], env_name: str | None = None) -> str:
    if requests is None:
        raise RuntimeError("requests 패키지가 없어 KIS API를 호출할 수 없습니다.")

    env_name = _coerce_env_name(env_name)
    headers = _build_kis_headers(
        tr_id="",
        env_name=env_name,
        extra_headers={"tr_id": ""},
    )
    _throttle_requests()
    response = requests.post(
        f"{_base_url(env_name)}{HASHKEY_ENDPOINT}",
        headers=headers,
        data=json.dumps(payload),
        timeout=15,
    )
    data = _safe_json(response)
    response.raise_for_status()
    hashkey = str(data.get("HASH") or "").strip()
    if not hashkey:
        raise RuntimeError(f"KIS hashkey 응답이 비어 있습니다: {data}")
    return hashkey


def _request_kis_json(
    *,
    endpoint: str,
    tr_id: str,
    params: dict[str, Any],
    env_name: str | None = None,
    tr_cont: str = "",
    return_headers: bool = False,
) -> dict[str, Any] | tuple[dict[str, Any], dict[str, str]]:
    if requests is None:
        raise RuntimeError("requests 패키지가 없어 KIS API를 호출할 수 없습니다.")

    env_name = _coerce_env_name(env_name)

    for attempt in range(4):
        _throttle_requests()
        response = requests.get(
            f"{_base_url(env_name)}{endpoint}",
            headers=_build_kis_headers(tr_id=tr_id, env_name=env_name, tr_cont=tr_cont),
            params=params,
            timeout=15,
        )
        data = _safe_json(response)

        if response.ok and data.get("rt_cd") in (None, "", "0"):
            if return_headers:
                return data, {str(key).lower(): value for key, value in response.headers.items()}
            return data

        if _is_rate_limited(data) and attempt < 3:
            time.sleep(0.5 * (attempt + 1))
            continue

        if _is_token_expired(data) and attempt < 3:
            _clear_cached_token(env_name)
            continue

        if not response.ok:
            message = data.get("msg1") or data.get("message") or response.text
            raise RuntimeError(f"KIS API 요청 실패: {message}")

        message = data.get("msg1") or data
        raise RuntimeError(f"KIS API 요청 실패: {message}")

    raise RuntimeError("KIS API 요청이 반복적으로 제한되었습니다. 잠시 후 다시 시도하세요.")


def _request_kis_post_json(
    *,
    endpoint: str,
    tr_id: str,
    body: dict[str, Any],
    env_name: str | None = None,
    include_hashkey: bool = False,
) -> dict[str, Any]:
    if requests is None:
        raise RuntimeError("requests 패키지가 없어 KIS API를 호출할 수 없습니다.")

    env_name = _coerce_env_name(env_name)
    headers = _build_kis_headers(tr_id=tr_id, env_name=env_name)
    if include_hashkey:
        try:
            headers["hashkey"] = _get_hashkey(body, env_name)
        except Exception:
            pass

    _throttle_requests()
    response = requests.post(
        f"{_base_url(env_name)}{endpoint}",
        headers=headers,
        data=json.dumps(body),
        timeout=15,
    )
    data = _safe_json(response)

    if response.ok and data.get("rt_cd") in (None, "", "0"):
        return data

    if _is_token_expired(data):
        _clear_cached_token(env_name)
        headers = _build_kis_headers(tr_id=tr_id, env_name=env_name)
        if include_hashkey:
            try:
                headers["hashkey"] = _get_hashkey(body, env_name)
            except Exception:
                pass
        _throttle_requests()
        response = requests.post(
            f"{_base_url(env_name)}{endpoint}",
            headers=headers,
            data=json.dumps(body),
            timeout=15,
        )
        data = _safe_json(response)
        if response.ok and data.get("rt_cd") in (None, "", "0"):
            return data

    if not response.ok:
        message = data.get("msg1") or data.get("message") or response.text
        raise RuntimeError(f"KIS API 요청 실패: {message}")

    message = data.get("msg1") or data
    raise RuntimeError(f"KIS API 요청 실패: {message}")


def _format_as_of(output: dict[str, Any]) -> str:
    trade_date = str(output.get("stck_bsop_date") or "").strip()
    trade_time = str(output.get("stck_cntg_hour") or "").strip()
    if len(trade_date) == 8 and len(trade_time) >= 6:
        return (
            f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]} "
            f"{trade_time[:2]}:{trade_time[2:4]}"
        )
    if len(trade_date) == 8:
        return f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _resolve_company_name_from_daily_chart(symbol: str) -> str:
    clean_symbol = symbol.strip()
    if not clean_symbol or using_sample_data():
        return _company_name(clean_symbol)

    end_date = date.today()
    start_date = end_date - timedelta(days=30)

    try:
        data = _request_kis_json(
            endpoint=DAILY_CHART_ENDPOINT,
            tr_id=DAILY_CHART_TR_ID,
            params={
                "FID_COND_MRKT_DIV_CODE": KIS_MARKET_CODE,
                "FID_INPUT_ISCD": clean_symbol,
                "FID_INPUT_DATE_1": start_date.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": end_date.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "1",
            },
        )
    except Exception:
        return _company_name(clean_symbol)

    output1 = data.get("output1", {}) or {}
    company_name = str(output1.get("hts_kor_isnm") or output1.get("prdt_name") or "").strip()
    return company_name or _company_name(clean_symbol)


def using_sample_data() -> bool:
    return _force_sample_data() or requests is None or len(_missing_env()) > 0


def get_runtime_status() -> dict[str, Any]:
    sample_mode = using_sample_data()
    return {
        "provider": "sample-placeholder" if sample_mode else "kis-openapi",
        "kis_env": _env_name(),
        "configured": len(_missing_env()) == 0,
        "missing_env": _missing_env(),
        "force_sample": _force_sample_data(),
        "requests_available": requests is not None,
    }


def get_current_price(symbol: str) -> dict[str, Any]:
    clean_symbol = symbol.strip()
    if not clean_symbol:
        return _sample_current_price(clean_symbol, source="sample-placeholder")

    if using_sample_data():
        return _sample_current_price(clean_symbol, source="sample-placeholder")

    data = _request_kis_json(
        endpoint=PRICE_ENDPOINT,
        tr_id=PRICE_TR_ID,
        params={
            "FID_COND_MRKT_DIV_CODE": KIS_MARKET_CODE,
            "FID_INPUT_ISCD": clean_symbol,
        },
    )
    output = data.get("output", {}) or {}
    current_price = _to_int(output.get("stck_prpr"))
    if current_price <= 0:
        raise RuntimeError(f"현재가 응답이 비어 있습니다: {output}")

    company_name = (
        output.get("hts_kor_isnm")
        or output.get("prdt_name")
        or _company_name(clean_symbol)
    )
    if company_name == _company_name(clean_symbol):
        company_name = _resolve_company_name_from_daily_chart(clean_symbol)

    return {
        "stock_code": output.get("stck_shrn_iscd") or clean_symbol,
        "company_name": company_name,
        "current_price": current_price,
        "change_pct": _to_float(output.get("prdy_ctrt")),
        "as_of": _format_as_of(output),
        "source": "kis-openapi",
    }


def get_price_history(symbol: str, start_date: date, end_date: date) -> list[dict[str, Any]]:
    if start_date > end_date:
        raise ValueError("시작일은 종료일보다 앞서야 합니다.")

    clean_symbol = symbol.strip()
    if not clean_symbol:
        return []

    if using_sample_data():
        return _sample_price_history(clean_symbol, start_date, end_date)

    data = _request_kis_json(
        endpoint=DAILY_CHART_ENDPOINT,
        tr_id=DAILY_CHART_TR_ID,
        params={
            "FID_COND_MRKT_DIV_CODE": KIS_MARKET_CODE,
            "FID_INPUT_ISCD": clean_symbol,
            "FID_INPUT_DATE_1": start_date.strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": end_date.strftime("%Y%m%d"),
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "1",
        },
    )
    records: list[dict[str, Any]] = []
    for row in data.get("output2", []) or []:
        trade_date = str(row.get("stck_bsop_date") or "").strip()
        if len(trade_date) != 8:
            continue
        records.append(
            {
                "date": f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}",
                "open": _to_float(row.get("stck_oprc")),
                "high": _to_float(row.get("stck_hgpr")),
                "low": _to_float(row.get("stck_lwpr")),
                "close": _to_float(row.get("stck_clpr")),
                "volume": _to_int(row.get("acml_vol")),
            }
        )

    if not records:
        raise RuntimeError("기간 시세 응답이 비어 있습니다.")
    return records


def _account_identifiers() -> tuple[str, str]:
    cano = os.getenv("KIS_ACCOUNT_NO", "").strip()
    acnt_prdt_cd = os.getenv("KIS_ACCOUNT_PRODUCT_CODE", "").strip()
    if not cano or not acnt_prdt_cd:
        raise RuntimeError("KIS 계좌번호 또는 계좌상품코드가 설정되어 있지 않습니다.")
    return cano, acnt_prdt_cd


def _balance_tr_id(env_name: str) -> str:
    return "TTTC8434R" if env_name == "real" else "VTTC8434R"


def _daily_ccld_tr_id(env_name: str) -> str:
    return "TTTC0081R" if env_name == "real" else "VTTC0081R"


def _order_cash_tr_id(env_name: str, side: str) -> str:
    if env_name == "real":
        return "TTTC0011U" if side == "sell" else "TTTC0012U"
    return "VTTC0011U" if side == "sell" else "VTTC0012U"


def _order_division_code(order_type: str) -> str:
    return "01" if order_type == "market" else "00"


def _format_order_timestamp(order_date: str, order_time: str) -> str:
    order_date = str(order_date or "").strip()
    order_time = str(order_time or "").strip()
    if len(order_date) == 8 and len(order_time) >= 6:
        return (
            f"{order_date[:4]}-{order_date[4:6]}-{order_date[6:8]} "
            f"{order_time[:2]}:{order_time[2:4]}:{order_time[4:6]}"
        )
    if len(order_date) == 8:
        return f"{order_date[:4]}-{order_date[4:6]}-{order_date[6:8]}"
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_balance_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    holdings: list[dict[str, Any]] = []
    for row in rows:
        qty = _to_int(row.get("hldg_qty"))
        available_qty = _to_int(row.get("ord_psbl_qty"))
        if qty <= 0 and available_qty <= 0:
            continue

        holdings.append(
            {
                "symbol": str(row.get("pdno") or "").strip(),
                "company_name": str(row.get("prdt_name") or "").strip(),
                "trade_type": str(row.get("trad_dvsn_name") or "").strip(),
                "qty": qty,
                "available_qty": available_qty,
                "avg_price": _to_float(row.get("pchs_avg_pric")),
                "purchase_amount": _to_int(row.get("pchs_amt")),
                "current_price": _to_float(row.get("prpr")),
                "evaluation_amount": _to_int(row.get("evlu_amt")),
                "profit_loss": _to_int(row.get("evlu_pfls_amt")),
                "profit_loss_rate": _to_float(row.get("evlu_pfls_rt")),
                "change_pct": _to_float(row.get("fltt_rt")),
            }
        )
    return holdings


def _normalize_balance_summary(summary_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "cash": _to_int(summary_row.get("dnca_tot_amt")),
        "next_day_cash": _to_int(summary_row.get("nxdy_excc_amt")),
        "securities_value": _to_int(summary_row.get("scts_evlu_amt")),
        "total_value": _to_int(summary_row.get("tot_evlu_amt")),
        "net_asset": _to_int(summary_row.get("nass_amt")),
        "purchase_amount_total": _to_int(summary_row.get("pchs_amt_smtl_amt")),
        "evaluation_amount_total": _to_int(summary_row.get("evlu_amt_smtl_amt")),
        "profit_loss_total": _to_int(summary_row.get("evlu_pfls_smtl_amt")),
        "asset_change_amount": _to_int(summary_row.get("asst_icdc_amt")),
        "asset_change_rate": _to_float(summary_row.get("asst_icdc_erng_rt")),
    }


def _normalize_order_row(row: dict[str, Any]) -> dict[str, Any]:
    qty = _to_int(row.get("ord_qty") or row.get("tot_ord_qty"))
    filled_qty = _to_int(row.get("tot_ccld_qty"))
    remaining_qty = _to_int(row.get("rmn_qty"))
    rejected_qty = _to_int(row.get("rjct_qty"))
    cancelled = str(row.get("cncl_yn") or "").strip().upper() == "Y"
    side = "sell" if str(row.get("sll_buy_dvsn_cd") or "").strip() == "01" else "buy"
    order_type_name = str(row.get("ord_dvsn_name") or "").strip()
    order_type = "market" if "시장가" in order_type_name else "limit"

    if rejected_qty > 0:
        status = "rejected"
    elif cancelled:
        status = "cancelled"
    elif remaining_qty > 0 and filled_qty > 0:
        status = "partial"
    elif remaining_qty > 0:
        status = "open"
    elif filled_qty > 0:
        status = "filled"
    else:
        status = "submitted"

    filled_price = _to_float(row.get("avg_prvs"))
    if filled_price <= 0 and filled_qty > 0:
        filled_price = _to_float(row.get("tot_ccld_amt")) / max(filled_qty, 1)

    return {
        "ordered_at": _format_order_timestamp(
            str(row.get("ord_dt") or ""),
            str(row.get("ord_tmd") or ""),
        ),
        "order_id": str(row.get("odno") or "").strip(),
        "symbol": str(row.get("pdno") or "").strip(),
        "company_name": str(row.get("prdt_name") or "").strip(),
        "side": side,
        "side_label": str(row.get("sll_buy_dvsn_cd_name") or "").strip() or ("매도" if side == "sell" else "매수"),
        "order_type": order_type,
        "order_type_label": order_type_name or ("시장가" if order_type == "market" else "지정가"),
        "qty": qty,
        "price": _to_float(row.get("ord_unpr")),
        "filled_qty": filled_qty,
        "filled_price": filled_price,
        "filled_amount": _to_int(row.get("tot_ccld_amt")),
        "remaining_qty": remaining_qty,
        "status": status,
        "status_label": {
            "rejected": "거부",
            "cancelled": "취소",
            "partial": "부분체결",
            "open": "미체결",
            "filled": "체결",
            "submitted": "접수",
        }.get(status, status),
        "cancelled": cancelled,
        "rejected_qty": rejected_qty,
        "exchange": str(row.get("excg_id_dvsn_cd") or row.get("excg_dvsn_cd") or "").strip(),
    }


def get_balance_snapshot(account_mode: str | None = None) -> dict[str, Any]:
    if using_sample_data():
        return {
            "summary": {
                "cash": 10000000,
                "securities_value": 0,
                "total_value": 10000000,
                "profit_loss_total": 0,
            },
            "holdings": [],
            "source": "sample-placeholder",
            "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    env_name = _coerce_env_name(account_mode)
    cano, acnt_prdt_cd = _account_identifiers()
    data = _request_kis_json(
        endpoint=BALANCE_ENDPOINT,
        tr_id=_balance_tr_id(env_name),
        env_name=env_name,
        params={
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "N",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        },
    )
    output1 = data.get("output1", []) or []
    output2 = data.get("output2", {}) or {}
    summary_row = output2[0] if isinstance(output2, list) and output2 else output2
    return {
        "summary": _normalize_balance_summary(summary_row if isinstance(summary_row, dict) else {}),
        "holdings": _normalize_balance_rows(output1),
        "source": f"kis-{env_name}",
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def get_order_history(account_mode: str | None = None, days: int = 7, only_open: bool = False) -> list[dict[str, Any]]:
    if using_sample_data():
        return []

    env_name = _coerce_env_name(account_mode)
    cano, acnt_prdt_cd = _account_identifiers()
    end_date = date.today()
    start_date = end_date - timedelta(days=max(days - 1, 0))
    data = _request_kis_json(
        endpoint=DAILY_CCLD_ENDPOINT,
        tr_id=_daily_ccld_tr_id(env_name),
        env_name=env_name,
        params={
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "INQR_STRT_DT": start_date.strftime("%Y%m%d"),
            "INQR_END_DT": end_date.strftime("%Y%m%d"),
            "SLL_BUY_DVSN_CD": "00",
            "PDNO": "",
            "CCLD_DVSN": "02" if only_open else "00",
            "INQR_DVSN": "00",
            "INQR_DVSN_3": "00",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
            "EXCG_ID_DVSN_CD": "KRX",
        },
    )
    rows = data.get("output1", []) or []
    orders = [_normalize_order_row(row) for row in rows]
    if only_open:
        orders = [order for order in orders if order["status"] in {"open", "partial", "submitted"}]
    return orders


def get_open_orders(account_mode: str | None = None, days: int = 7) -> list[dict[str, Any]]:
    return get_order_history(account_mode=account_mode, days=days, only_open=True)


def get_account_snapshot(account_mode: str | None = None, days: int = 7) -> dict[str, Any]:
    balance_snapshot = get_balance_snapshot(account_mode=account_mode)
    return {
        "balance_snapshot": balance_snapshot,
        "portfolio_items": balance_snapshot.get("holdings", []),
        "order_items": get_order_history(account_mode=account_mode, days=days),
        "open_order_items": get_open_orders(account_mode=account_mode, days=days),
        "synced_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def submit_order(
    *,
    account_mode: str,
    symbol: str,
    side: str,
    order_type: str,
    qty: int,
    price: float | None = None,
) -> dict[str, Any]:
    """Submit an order.

    Returns a dict with keys: status, order_id, executed_price, executed_qty, message, raw_response
    """
    symbol = (symbol or "").strip()
    env_name = _coerce_env_name(account_mode)
    if not symbol:
        return {
            "status": "failed",
            "order_id": None,
            "executed_price": 0,
            "executed_qty": 0,
            "message": "주문할 종목코드가 비어 있습니다.",
            "raw_response": {},
        }
    if int(qty) <= 0:
        return {
            "status": "failed",
            "order_id": None,
            "executed_price": 0,
            "executed_qty": 0,
            "message": "주문 수량은 1주 이상이어야 합니다.",
            "raw_response": {},
        }
    if order_type == "limit" and float(price or 0) <= 0:
        return {
            "status": "failed",
            "order_id": None,
            "executed_price": 0,
            "executed_qty": 0,
            "message": "지정가 주문은 0원보다 큰 가격이 필요합니다.",
            "raw_response": {},
        }

    if using_sample_data():
        # Simulate order
        order_id = hashlib.sha1(f"{symbol}:{side}:{order_type}:{qty}:{time.time()}".encode()).hexdigest()[:12]
        current = None
        try:
            current = _sample_current_price(symbol, source="sample-placeholder").get("current_price")
        except Exception:
            current = _base_price(symbol)

        if order_type == "market":
            executed_price = float(current or 0)
            status = "filled"
            executed_qty = int(qty)
            message = "모의시장가 주문이 체결되었습니다."
        else:
            # limit order: if price equals current simulate fill, else remain open
            executed_price = float(price or 0)
            if executed_price == float(current):
                status = "filled"
                executed_qty = int(qty)
                message = "모의지정가 주문이 즉시 체결되었습니다."
            else:
                status = "open"
                executed_qty = 0
                message = "모의지정가 주문이 접수되어 체결 대기 중입니다."

        return {
            "status": status,
            "order_id": order_id,
            "executed_price": executed_price,
            "executed_qty": executed_qty,
            "message": message,
            "raw_response": {"simulated": True},
        }

    try:
        cano, acnt_prdt_cd = _account_identifiers()
        ord_dvsn = _order_division_code(order_type)
        order_price = "0" if ord_dvsn == "01" else str(int(round(price or 0)))
        response = _request_kis_post_json(
            endpoint=ORDER_CASH_ENDPOINT,
            tr_id=_order_cash_tr_id(env_name, side),
            env_name=env_name,
            include_hashkey=True,
            body={
                "CANO": cano,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "PDNO": symbol,
                "ORD_DVSN": ord_dvsn,
                "ORD_QTY": str(int(qty)),
                "ORD_UNPR": order_price,
                "EXCG_ID_DVSN_CD": "KRX",
                "SLL_TYPE": "01" if side == "sell" else "",
                "CNDT_PRIC": "",
            },
        )
        output = response.get("output", {}) or {}
        order_id = str(output.get("ODNO") or output.get("odno") or "").strip() or None
        order_time = str(output.get("ORD_TMD") or output.get("ord_tmd") or "").strip()
        return {
            "status": "submitted",
            "order_id": order_id,
            "order_time": order_time,
            "executed_price": 0,
            "executed_qty": 0,
            "message": (
                f"{'모의투자' if env_name == 'demo' else '실전투자'} 주문이 접수되었습니다."
                if order_id
                else "주문이 접수되었습니다."
            ),
            "raw_response": response,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "order_id": None,
            "executed_price": 0,
            "executed_qty": 0,
            "message": f"주문 요청 실패: {exc}",
            "raw_response": {},
        }
