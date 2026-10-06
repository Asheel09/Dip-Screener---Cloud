import os
import unittest

os.environ.setdefault("MARKET_DATA_PROVIDER", "demo")
os.environ.setdefault("NEWS_PROVIDER", "demo")

from fastapi.testclient import TestClient
from app import app

class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_health(self):
        d = self.client.get('/api/health').json()
        self.assertTrue(d['ok'])
        self.assertEqual(d['version'], '3.1.0')
        self.assertIn('ai_configured', d)

    def test_live_has_rows_and_settings(self):
        d = self.client.get('/api/live').json()
        self.assertGreater(len(d['rows']), 5)
        self.assertIn('severity', d['rows'][0])
        self.assertIn('settings', d)

    def test_research(self):
        d = self.client.get('/api/research/AMD').json()
        self.assertEqual(d['symbol'], 'AMD')
        self.assertIn('news', d)
        self.assertIn('history', d)
        self.assertIn('recovery', d)
        self.assertIn('description', d)
        self.assertIn('news_live', d)


    def test_research_summary_is_local_shape(self):
        d = self.client.get('/api/research-summary/AMD').json()
        self.assertEqual(d['symbol'], 'AMD')
        self.assertIn('recovery', d)
        self.assertIn('history', d)
        self.assertEqual(d['news'], [])

    def test_screeners_include_ai(self):
        d = self.client.get('/api/screeners').json()
        ids = {x['id'] for x in d}
        self.assertIn('live-dips', ids)
        self.assertIn('ai-assistant', ids)
        self.assertIn('customize', ids)
        self.assertIn('broad-market', ids)

    def test_settings_live_update_and_restore(self):
        old = self.client.get('/api/settings').json()['settings']
        r = self.client.put('/api/settings', json={'changes': {'push_ms': 500, 'compact_mode': True}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['settings']['push_ms'], 500)
        self.assertTrue(r.json()['settings']['compact_mode'])
        self.client.put('/api/settings', json={'changes': old})



    def test_broad_market_is_separate(self):
        d = self.client.get('/api/market-overview').json()
        self.assertIn('Indexes', d['groups'])
        self.assertIn('Sectors', d['groups'])
        self.assertGreaterEqual(len(d['groups']['Indexes']), 4)
        live = self.client.get('/api/live').json()['rows']
        self.assertTrue(all(x.get('kind', 'stock') != 'market' for x in live))

    def test_default_universe_is_larger_than_twelve(self):
        d = self.client.get('/api/market-overview').json()
        total = d['status']['total_symbols']
        self.assertGreaterEqual(total, 500)

    def test_health_exposes_market_status(self):
        d = self.client.get('/api/health').json()
        self.assertIn('market_status', d)
        self.assertIn('state', d['market_status'])


    def test_mover_radar_demo_shape(self):
        d = self.client.get('/api/mover-radar?region=all').json()
        self.assertIn('rows', d)
        self.assertGreater(len(d['rows']), 0)
        self.assertIn('move', d['rows'][0])

    def test_mover_radar_is_in_navigation(self):
        d = self.client.get('/api/screeners').json()
        mover = next(x for x in d if x['id'] == 'movers')
        self.assertEqual(mover['name'], 'Mover Radar')

    def test_europe_scanner_locations_include_paris(self):
        from services.ibkr_webapi import IBKRWebAPI
        params = {"location_tree": [{"display_name": "European Stocks", "type": "STOCK.EU", "locations": [
            {"display_name": "Paris", "type": "STK.EU.SBF", "locations": []},
            {"display_name": "Xetra", "type": "STK.EU.IBIS", "locations": []}
        ]}]}
        configs = IBKRWebAPI._scanner_region_configs(params, 'europe')
        self.assertIn('STK.EU.SBF', {x['location'] for x in configs})

    def test_catalyst_classifier_handles_target_events(self):
        from services.free_news import _classify
        self.assertEqual(_classify('Schneider Electric falls after acquisition deal'), 'corporate')
        self.assertEqual(_classify('Seagate slides as rival expands production capacity'), 'supply-demand')
        self.assertEqual(_classify('Broker downgrades shares and cuts price target'), 'analyst')


    def test_yahoo_session_move_prefers_premarket(self):
        from services.yahoo_market import YahooMarketAPI
        q = {
            'marketState': 'PRE', 'regularMarketPreviousClose': 100,
            'regularMarketPrice': 100, 'preMarketPrice': 94,
            'preMarketChangePercent': -6.0
        }
        session, move, reference, extended = YahooMarketAPI._screen_move(q)
        self.assertEqual(session, 'PREMARKET')
        self.assertEqual(move, -6.0)
        self.assertEqual(reference, 100)
        self.assertEqual(extended, 94)

    def test_yahoo_closed_market_keeps_regular_reference(self):
        from services.yahoo_market import YahooMarketAPI
        q = {'marketState': 'CLOSED', 'regularMarketPrice': 661.75, 'regularMarketPreviousClose': 592.47, 'regularMarketChangePercent': 11.69}
        session, move, reference, extended = YahooMarketAPI._screen_move(q)
        self.assertEqual(session, 'PREVIOUS_CLOSE')
        self.assertEqual(reference, 661.75)
        self.assertIsNone(extended)

    def test_ai_without_key_is_safe(self):
        # In CI/local test environments without a key, this should fail clearly rather than expose anything.
        h = self.client.get('/api/health').json()
        if not h['ai_configured']:
            r = self.client.post('/api/ai/ask', json={'question': 'test'})
            self.assertEqual(r.status_code, 503)

if __name__ == '__main__':
    unittest.main()
