import unittest
from unittest.mock import patch

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
            "type", "typespecs", "market",
        ]
        def row(s, values):
            return {"s": s, "d": [values.get(c) for c in cols]}
        return {"data": [
            row("NASDAQ:LOSS", {"name":"LOSS","description":"Loser Corp","close":40.0,"change":-9.2,"volume":1200000,"relative_volume_10d_calc":2.1,"market_cap_basic":10_000_000_000,"currency":"USD","type":"stock","typespecs":["common"],"market":"america"}),
            row("NYSE:GAIN", {"name":"GAIN","description":"Gainer Corp","close":60.0,"change":7.4,"volume":900000,"relative_volume_10d_calc":1.8,"market_cap_basic":15_000_000_000,"currency":"USD","type":"stock","typespecs":["common"],"market":"america"}),
            row("NASDAQ:SMALL", {"name":"SMALL","description":"Small Move","close":50.0,"change":0.5,"volume":100000,"market_cap_basic":10_000_000_000,"currency":"USD","type":"stock","typespecs":["common"],"market":"america"}),
            row("EURONEXT:EU", {"name":"EU","description":"Europe Corp","close":50.0,"change":-12.0,"volume":100000,"market_cap_basic":10_000_000_000,"currency":"EUR","type":"stock","typespecs":["common"],"market":"france"}),
        ]}


class _Client:
    def __init__(self, *a, **kw):
        pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def post(self, *a, **kw): return _Resp()


class TradingViewMoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_us_two_sided_scan_and_exchange_filter(self):
        api = TradingViewMoverAPI()
        with patch('services.tradingview_market.httpx.AsyncClient', _Client):
            result = await api.run_mover_scan('us', limit=20, force=True)
        self.assertEqual(result['provider'], 'tradingview-screener')
        symbols = {x['symbol'] for x in result['rows']}
        self.assertEqual(symbols, {'LOSS', 'GAIN'})
        moves = {x['symbol']: x['move'] for x in result['rows']}
        self.assertAlmostEqual(moves['LOSS'], -9.2)
        self.assertAlmostEqual(moves['GAIN'], 7.4)
        self.assertTrue(all(x['region'] == 'US' for x in result['rows']))
        self.assertTrue(all(x['reason'] == 'No verified catalyst found' for x in result['rows']))

    async def test_europe_is_rejected(self):
        api = TradingViewMoverAPI()
        with self.assertRaises(ValueError):
            await api.run_mover_scan('europe', force=True)


if __name__ == '__main__':
    unittest.main()
