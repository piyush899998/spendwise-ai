import json
from decimal import Decimal

from src import market_data


def setup_function():
    market_data._cache.clear()


def test_gold_snapshot_validates_decimal_prices_and_caches(monkeypatch):
    calls = []

    def fake_fetch(url, *, accept):
        calls.append(url)
        return (b'{"currency":"INR","timestamp":"2026-11-11T08:00:00Z","price_gram_24k":"7345.50","price_gram_22k":"6733.38"}', 200)

    monkeypatch.setattr(market_data, '_fetch', fake_fetch)
    first = market_data.gold_snapshot()
    second = market_data.gold_snapshot()
    assert first['value_paise'] == 734550
    assert first['price_22k_paise'] == 673338
    assert second == first
    assert len(calls) == 1


def test_market_series_calculates_change_only_from_consecutive_observations(monkeypatch):
    payload = {'data': [
        {'date': '2026-11-10', '^NSEI': '100', '^BSESN': '200', '^NSEBANK': '300', 'USDINR': '80'},
        {'date': '2026-11-11', '^NSEI': '102', '^BSESN': '198', '^NSEBANK': '303', 'USDINR': '80.5'},
    ]}
    monkeypatch.setattr(market_data, '_fetch', lambda *args, **kwargs: (str(payload).replace("'", '"').encode(), 200))
    result = market_data.market_series()
    nifty = next(item for item in result['instruments'] if item['symbol'] == '^NSEI')
    assert nifty['value'] == 102
    assert nifty['change'] == 2
    assert round(nifty['change_percent'], 2) == 2.0
    assert len(nifty['history']) == 2


def test_news_snapshot_sanitizes_xml_and_rejects_non_http_links(monkeypatch):
    xml = b'''<rss><channel><item><title>India &amp; markets</title><link>https://example.com/story</link><pubDate>Tue, 11 Nov 2026 08:00:00 GMT</pubDate><source>Example News</source></item><item><title>Unsafe</title><link>javascript:alert(1)</link></item></channel></rss>'''
    monkeypatch.setattr(market_data, '_fetch', lambda *args, **kwargs: (xml, 200))
    result = market_data.news_snapshot()
    assert result['items'] == [{'headline': 'India & markets', 'publisher': 'Example News', 'published_at': 'Tue, 11 Nov 2026 08:00:00 GMT', 'url': 'https://example.com/story'}]


def test_market_pulse_isolates_provider_failures(monkeypatch):
    def fail():
        raise market_data.MarketProviderError('upstream_unreachable')

    monkeypatch.setattr(market_data, 'gold_snapshot', fail)
    monkeypatch.setattr(market_data, 'market_series', fail)
    monkeypatch.setattr(market_data, 'news_snapshot', fail)
    result = market_data.market_pulse(force_refresh=True)
    assert result['status'] == 'unavailable'
    assert result['gold']['value_paise'] is None
    assert result['indices'] == []
    assert result['news'] == []
    assert set(result['provider_errors']) == {'gold', 'gold_history', 'indices', 'news'}


def test_gold_history_converts_only_matching_dated_fx_observations(monkeypatch):
    calls = []

    def fake_fetch(url, *, accept):
        calls.append(url)
        if 'standardbullion.com' in url:
            return (json.dumps({'points': [
                {'t': '2026-11-10T00:00:00Z', 'price': 2000},
                {'t': '2026-11-11T00:00:00Z', 'price': 2025},
                {'t': '2026-11-09T00:00:00Z', 'price': 2030},
            ]}).encode(), 200)
        return (json.dumps([
            {'date': '2026-11-10', 'base': 'USD', 'quote': 'INR', 'rate': 80},
            {'date': '2026-11-11', 'base': 'USD', 'quote': 'INR', 'rate': 80.5},
        ]).encode(), 200)

    monkeypatch.setattr(market_data, '_fetch', fake_fetch)
    result = market_data.gold_history('7d')
    assert result['status'] == 'available'
    assert len(result['history']) == 2
    expected = int((Decimal('2000') * Decimal('80') / Decimal('31.1035') * 100).quantize(Decimal('1')))
    assert result['history'][0]['value_paise'] == expected
    assert result['dropped_unmatched_dates'] == 1
    assert len(calls) == 2
