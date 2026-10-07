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
        self.assertEqual(d['version'], '3.4.0')
        self.assertIn('market_status', d)

    def test_live_has_rows_and_settings(self):
        d = self.client.get('/api/live').json()
        self.assertGreater(len(d['rows']), 5)
        self.assertIn('severity', d['rows'][0])
        self.assertIn('settings', d)

    def test_research_summary_is_local_shape(self):
        d = self.client.get('/api/research-summary/AMD').json()
        self.assertEqual(d['symbol'], 'AMD')
        self.assertIn('recovery', d)
        self.assertIn('history', d)
        self.assertEqual(d['news'], [])

    def test_navigation_is_trimmed(self):
        d = self.client.get('/api/screeners').json()
        ids = {x['id'] for x in d}
        self.assertIn('movers', ids)
        self.assertIn('broad-market', ids)
        self.assertIn('news', ids)
        self.assertIn('watchlist', ids)
        self.assertIn('backtests', ids)
        self.assertNotIn('live-dips', ids)
        self.assertNotIn('comparable-drops', ids)
        self.assertNotIn('ai-assistant', ids)
        self.assertNotIn('customize', ids)
        mover = next(x for x in d if x['id'] == 'movers')
        self.assertEqual(mover['name'], 'Movers')
        news = next(x for x in d if x['id'] == 'news')
        self.assertEqual(news['name'], 'Stock News')

    def test_settings_live_update_and_restore(self):
        old = self.client.get('/api/settings').json()['settings']
        r = self.client.put('/api/settings', json={'changes': {'push_ms': 500, 'compact_mode': True}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['settings']['push_ms'], 500)
        self.assertTrue(r.json()['settings']['compact_mode'])
        self.client.put('/api/settings', json={'changes': old})

    def test_broad_market_has_indexes_and_sectors(self):
        d = self.client.get('/api/market-overview').json()
        self.assertIn('Indexes', d['groups'])
        self.assertIn('Sectors', d['groups'])
        self.assertGreaterEqual(len(d['groups']['Indexes']), 4)

    def test_default_universe_is_large(self):
        d = self.client.get('/api/market-overview').json()
        self.assertGreaterEqual(d['status']['total_symbols'], 500)

    def test_mover_radar_demo_has_both_reason_status_and_us_only(self):
        d = self.client.get('/api/mover-radar?region=us').json()
        self.assertIn('rows', d)
        self.assertGreater(len(d['rows']), 0)
        self.assertTrue(all(x.get('region') == 'US' for x in d['rows']))
        self.assertTrue(all('reason' in x for x in d['rows']))

    def test_europe_region_is_rejected(self):
        r = self.client.get('/api/mover-radar?region=europe')
        self.assertEqual(r.status_code, 400)

    def test_relay_discards_europe_and_preserves_verified_reason(self):
        from app import state
        state.set_relay_movers([
            {'symbol':'SU','name':'Schneider Electric','region':'Europe','exchange':'EURONEXT','move':-9.2,'price':250,'session':'REGULAR'},
            {'symbol':'STX','name':'Seagate','region':'US','exchange':'NASDAQ','move':-8.1,'price':200,'session':'PREMARKET',
             'reason':'Seagate slides after company guidance update','cause_type':'guidance','news_url':'https://www.reuters.com/technology/seagate-guidance-example','reason_verified':True},
            {'symbol':'XYZ','name':'Example','region':'US','exchange':'NYSE','move':6.2,'price':40,'session':'REGULAR',
             'reason':'No verified catalyst found','cause_type':'unverified','reason_verified':False},
            {'symbol':'ABSI','name':'Absci','region':'US','exchange':'NASDAQ','move':-10.9,'price':10.2,'session':'REGULAR',
             'reason':"It turns out I live near Absci's headquarters 😊",'cause_type':'corporate','news_url':'https://www.reuters.com/example','reason_verified':True},
        ], source='test-relay')
        scan = state._relay_scan('us')
        self.assertEqual(scan['provider'], 'local-relay')
        self.assertEqual({x['symbol'] for x in scan['rows']}, {'STX','XYZ','ABSI'})
        stx = next(x for x in scan['rows'] if x['symbol']=='STX')
        self.assertTrue(stx['reason_verified'])
        self.assertEqual(stx['cause_type'], 'guidance')
        absi = next(x for x in scan['rows'] if x['symbol']=='ABSI')
        self.assertFalse(absi['reason_verified'])
        self.assertEqual(absi['reason'], 'No verified catalyst found')
        self.assertEqual(absi['news_url'], '')

    def test_catalyst_classifier_and_source_filters(self):
        from services.free_news import _classify, company_headline_relevant, source_allowed
        self.assertEqual(_classify('Company reports earnings and revenue'), 'earnings')
        self.assertEqual(_classify('Biotech announces Phase 2 clinical trial'), 'clinical')
        self.assertFalse(source_allowed('https://www.investing.com/news/example'))
        self.assertFalse(source_allowed('https://www.reddit.com/r/stocks/example'))
        self.assertTrue(source_allowed('https://www.reuters.com/business/example'))
        self.assertFalse(company_headline_relevant("It turns out I live near Absci's headquarters 😊", 'Absci', 'ABSI'))
        self.assertTrue(company_headline_relevant('Absci announces Phase 2 trial plan', 'Absci', 'ABSI'))

    def test_yahoo_session_move_prefers_premarket(self):
        from services.yahoo_market import YahooMarketAPI
        q = {'marketState': 'PRE', 'regularMarketPreviousClose': 100, 'regularMarketPrice': 100, 'preMarketPrice': 94, 'preMarketChangePercent': -6.0}
        session, move, reference, extended = YahooMarketAPI._screen_move(q)
        self.assertEqual((session, move, reference, extended), ('PREMARKET', -6.0, 100, 94))


if __name__ == '__main__':
    unittest.main()
