import importlib.util
import pathlib
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('meridian_relay', ROOT / 'meridian_relay.py')
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)

class RelayV36Tests(unittest.TestCase):
    def test_context_fields_and_baba_themes(self):
        vals = {c: None for c in relay.COLS}
        vals.update({
            'name': 'BABA', 'description': 'Alibaba Group Holding Limited',
            'close': 150.0, 'change': -4.5, 'volume': 10_000_000,
            'relative_volume_10d_calc': 1.4, 'market_cap_basic': 350_000_000_000,
            'currency': 'USD', 'type': 'stock', 'typespecs': ['common'],
            'market': 'america', 'sector': 'Retail Trade', 'industry': 'Internet Retail',
            'Perf.W': -7.2, 'Perf.1M': 5.8,
        })
        body = {'data':[{'s':'NYSE:BABA','d':[vals[c] for c in relay.COLS]}]}
        with patch.object(relay, 'us_session', return_value='CLOSED'), patch.object(relay, 'post_json', return_value=body):
            rows = relay.scan(sort_order='asc', limit=25)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['sector'], 'Retail Trade')
        self.assertEqual(row['industry'], 'Internet Retail')
        self.assertEqual(row['week_move'], -7.2)
        self.assertEqual(row['month_move'], 5.8)
        relay.attach_themes(rows)
        self.assertEqual(row['themes'][:3], ['E-commerce', 'Cloud', 'AI'])


    def test_sector_relative_context(self):
        rows = [{"symbol":"BABA","sector":"Retail Trade","move":-7.0}]
        relay.attach_sector_context(rows, {"AMEX:XLY": -2.0})
        self.assertEqual(rows[0]["sector_benchmark"], "XLY")
        self.assertEqual(rows[0]["sector_move"], -2.0)
        self.assertEqual(rows[0]["sector_relative_move"], -5.0)

    def test_scan_delta_only_uses_recent_state(self):
        rows = [{"symbol":"BABA","tv_symbol":"NYSE:BABA","move":-6.0}]
        relay.attach_scan_deltas(rows, {"collected_at": 123.0, "moves":{"NYSE:BABA":-4.0}})
        self.assertEqual(rows[0]["previous_move"], -4.0)
        self.assertEqual(rows[0]["scan_delta"], -2.0)
        self.assertEqual(rows[0]["previous_scan_at"], 123.0)

    def test_catalyst_quality(self):
        self.assertEqual(relay.catalyst_quality(90), "High")
        self.assertEqual(relay.catalyst_quality(70), "Medium")
        self.assertEqual(relay.catalyst_quality(30), "Standard")

    def test_default_interval_is_hourly(self):
        self.assertEqual(relay.DEFAULT_INTERVAL_SECONDS, 3600)

if __name__ == '__main__':
    unittest.main()
