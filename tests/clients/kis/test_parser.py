"""KIS 실시간 데이터 파서 테스트."""

from app.clients.kis.parser import parse_orderbook, parse_trade
from tests.fakes import kis_orderbook_body, kis_trade_body


def test_parse_trade_maps_fields():
    trade = parse_trade(kis_trade_body())

    assert trade == {
        "symbol": "005930",
        "time": "153000",
        "price": 58300,
        "changeSign": "2",
        "change": 200,
        "changePercent": 0.34,
        "open": 58000,
        "high": 58500,
        "low": 57800,
        "prevClose": None,
        "volume": 5000000,
        "amount": 290000000000,
    }


def test_parse_trade_none_when_fields_missing():
    assert parse_trade("005930^153000^58300") is None
    assert parse_trade("") is None


def test_parse_trade_non_numeric_field_is_none():
    trade = parse_trade(kis_trade_body(f2="N/A"))

    assert trade is not None
    assert trade["price"] is None
    assert trade["symbol"] == "005930"


def test_parse_orderbook_maps_fields():
    orderbook = parse_orderbook(kis_orderbook_body())

    assert orderbook is not None
    assert orderbook["askPrices"][:2] == [58300, 58310]
    assert orderbook["bidPrices"][:2] == [58290, 58280]
    assert orderbook["askVolumes"][0] == 1000
    assert orderbook["bidVolumes"][0] == 900
    assert (orderbook["totalAskVolume"], orderbook["totalBidVolume"]) == (55000, 49500)


def test_parse_orderbook_none_when_fields_missing():
    assert parse_orderbook("^".join(["0"] * 44)) is None
