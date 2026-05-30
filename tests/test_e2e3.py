import sys, os, json
sys.path.insert(0, os.getcwd())

# Test E2
def safe_parse_nexus(res_json: dict) -> dict:
    raw = res_json.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
    text = raw.strip()
    if not text:
        return {'top_candidates': [], 'correlations': []}
    if text.startswith('```'):
        lines = text.split('\n')
        text = '\n'.join(lines[1:-1] if lines[-1].strip() == '```' else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {'top_candidates': [], 'correlations': []}

assert safe_parse_nexus({}) == {'top_candidates': [], 'correlations': []}
assert safe_parse_nexus({'candidates': [{'content': {'parts': [{'text': ''}]}}]}) == {'top_candidates': [], 'correlations': []}
md_response = {'candidates': [{'content': {'parts': [{'text': '```json\n{"top_candidates": ["123"], "correlations": []}\n```'}]}}]}
result = safe_parse_nexus(md_response)
assert result['top_candidates'] == ['123']
ok_response = {'candidates': [{'content': {'parts': [{'text': '{"top_candidates": ["abc"], "correlations": []}'}]}}]}
assert safe_parse_nexus(ok_response)['top_candidates'] == ['abc']

# Test E3
from agents.shared.adapters.polymarket import PolymarketAdapter
from datetime import datetime

adapter = PolymarketAdapter()
item1 = {'endDate': '2026-06-01T00:00:00Z'}
dt1 = adapter._get_end_date(item1)
assert dt1.year == 2026

item2 = {'end_date_iso': '2026-07-15T12:00:00Z'}
dt2 = adapter._get_end_date(item2)
assert dt2.month == 7

item3 = {'id': '692250', 'question': 'Test?'}
dt3 = adapter._get_end_date(item3)
assert dt3.year == 2099

print('ALL E2 AND E3 TESTS PASSED!')
