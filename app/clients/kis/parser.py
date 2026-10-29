"""KIS 실시간 데이터 본문(^ 구분 필드열) → dict. 키 이름은 SSE·스냅샷 응답 계약이다."""

import logging
from typing import Any

logger = logging.getLogger(__name__)

_TRADE_FIELDS = 15
_ORDERBOOK_FIELDS = 45


def parse_trade(body: str) -> dict[str, Any] | None:
    """체결가(H0STCNT0). 필드가 모자라면 None, 숫자가 아닌 값은 그 필드만 None.

    필드(0부터): 0 종목코드, 1 체결시간(HHMMSS), 2 현재가, 3 전일대비부호(1 상한 2 상승 3 보합 4 하한 5 하락),
    4 전일대비, 5 전일대비율, 6 가중평균가, 7 시가, 8 고가, 9 저가, 10 매도호가1, 11 매수호가1,
    12 체결거래량, 13 누적거래량, 14 누적거래대금.
    """
    fields = body.split("^")
    if len(fields) < _TRADE_FIELDS:
        logger.warning("체결가 필드 수 부족: %d개 (%d개 이상이어야 한다)", len(fields), _TRADE_FIELDS)
        return None
    return {
        "symbol": fields[0].strip(),
        "time": fields[1].strip(),
        "price": _to_int(fields[2]),
        "changeSign": fields[3].strip(),
        "change": _to_int(fields[4]),
        "changePercent": _to_float(fields[5]),
        "open": _to_int(fields[7]),
        "high": _to_int(fields[8]),
        "low": _to_int(fields[9]),
        "prevClose": None,  # 체결 데이터에 없다. 응답 계약상 키만 둔다
        "volume": _to_int(fields[13]),
        "amount": _to_int(fields[14]),
    }


def parse_orderbook(body: str) -> dict[str, Any] | None:
    """호가(H0STASP0). 필드가 모자라면 None, 숫자가 아닌 값은 그 자리만 None.

    필드(0부터): 0 종목코드, 1 영업시간, 2 시간구분코드, 3~12 매도호가1~10, 13~22 매수호가1~10,
    23~32 매도잔량1~10, 33~42 매수잔량1~10, 43 총매도호가잔량, 44 총매수호가잔량.
    """
    fields = body.split("^")
    if len(fields) < _ORDERBOOK_FIELDS:
        logger.warning("호가 필드 수 부족: %d개 (%d개 이상이어야 한다)", len(fields), _ORDERBOOK_FIELDS)
        return None
    return {
        "symbol": fields[0].strip(),
        "time": fields[1].strip(),
        "askPrices": [_to_int(value) for value in fields[3:13]],
        "askVolumes": [_to_int(value) for value in fields[23:33]],
        "bidPrices": [_to_int(value) for value in fields[13:23]],
        "bidVolumes": [_to_int(value) for value in fields[33:43]],
        "totalAskVolume": _to_int(fields[43]),
        "totalBidVolume": _to_int(fields[44]),
    }


def _to_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _to_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None
