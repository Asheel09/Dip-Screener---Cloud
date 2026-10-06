import unittest
from unittest.mock import AsyncMock, patch

from services.tradingview_market import TradingViewMoverAPI


class _Resp:
    def raise_for_status(self):
        return None
    def json(self):
        cols = [
            "name", "description", "close", "change", "volume",
            "relative_volume_10d_calc", "market_cap_basic", "currency",
            "premarket_close", "premarket_change", "premarket_volume",
            "postmarket_close", "postmarket_change", "postmarket_volume",
            "type", "typespecs",
        ]
        def row(s, values):
            return {"s": s, "d": [values.get(c) for c in cols]}
        return {"data": [
            row("EURONEXT:SU", {"name":"SU","description":"Schneider Electric SE","close":240.0,"change":-9.2,"volume":1200000,"relative_volume_10d_calc":2.1,"market_cap_basic":140_000_000_000,"currency":"EUR","type":"stock","typespecs":["common"]}),
            row("EURONEXT:ABC", {"name":"ABC","description":"Small Move","close":50.0,"change":-0.5,"volume":100000,"market_cap_basic":10_000_000_000,"currency":"EUR","type":"stock","typespecs":["common"]}),
        ]}


class _Client:
    def __init__(self, *a, **kw):
        pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def post(self, *a, **kw): return _Resp()


class TradingViewMoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_europe_loser_parses(self):
        api = TradingViewMoverAPI()
        with patch('services.tradingview_market.httpx.AsyncClient', _Client):
            result = await api.run_mover_scan('europe', limit=20, force=True)
        self.assertEqual(result['provider'], 'tradingview-screener')
        self.assertEqual(len(result['rows']), 1)
        self.assertEqual(result['rows'][0]['symbol'], 'SU')
        self.assertAlmostEqual(result['rows'][0]['move'], -9.2)
        self.assertEqual(result['rows'][0]['region'], 'Europe')

if __name__ == '__main__':
    unittest.main()
