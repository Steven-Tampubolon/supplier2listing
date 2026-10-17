import json, re, unicodedata
from pathlib import Path

CRITICAL = {'gtin', 'cost_price', 'stock', 'brand', 'warranty'}
HIGH = {'category', 'size', 'material', 'ingredients', 'net_content', 'weight_item', 'weight_package', 'dims_item',
        'color', 'capacity', 'length', 'resolution', 'mpn', 'supplier_sku'}
FLAG_STATUSES = {'conflict', 'needs_review', 'error'}
ALL_STATUSES = {'extracted', 'normalized', 'inferred', 'missing', 'conflict', 'error', 'needs_review'}
# field yang tidak pernah dinilai sebagai nilai
NEVER_SCORED = {'description', 'supplier_sku'}
GOOD = {'correct', 'correct_empty', 'flagged_correct'}
WRONG_FILL = {'wrong_fill', 'unsupported_fill', 'silent_resolution', 'fabricated_correction'}
ISSUE_CODES = ['REQUIRED_ATTR_MISSING', 'GTIN_MISSING', 'GTIN_INVALID', 'WEIGHT_SCOPE_AMBIGUOUS', 'VALUE_CONFLICT',
               'TITLE_ATTR_CONTRADICTION', 'DUPLICATE_SKU', 'CLAIM_UNSUBSTANTIATED', 'CATEGORY_AMBIGUOUS', 'ATTR_UNREADABLE']

def tier(field):
    if field in CRITICAL: return 'kritis'
    if field in HIGH: return 'tinggi'
    return 'standar'

def read_jsonl(path):
    with open(path, encoding='utf-8') as f:
        return [json.loads(l) for l in f if l.strip()]

def write_jsonl(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False) + '\n')

def norm_space(s):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(s))).strip()

def norm_text(s):
    s = norm_space(s).casefold()
    return s.strip(' .,;:')

def norm_key(s):
    return norm_space(s).casefold()
