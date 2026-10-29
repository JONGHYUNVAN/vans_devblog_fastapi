"""KIS OpenAPI 주소·TR ID 와 대시보드 종목 이름."""

# 대시보드가 추적하는 국내 종목. 없는 종목은 이름 대신 종목코드를 쓴다.
KR_STOCK_MAP = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "005380": "현대차",
}

KIS_WS_URL_REAL = "ws://ops.koreainvestment.com:21000"
KIS_WS_URL_MOCK = "ws://ops.koreainvestment.com:31000"

KIS_REST_URL_REAL = "https://openapi.koreainvestment.com:9443"
KIS_REST_URL_MOCK = "https://openapivts.koreainvestment.com:29443"

TR_ID_TRADE = "H0STCNT0"  # 실시간 체결가
TR_ID_ORDERBOOK = "H0STASP0"  # 실시간 호가
