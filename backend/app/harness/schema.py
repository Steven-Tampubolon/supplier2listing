"""Kontrak keluaran model + parser longgar."""
import json, re
from .common import ALL_STATUSES, norm_key

OUTPUT_SCHEMA = {
    "type": "object", "required": ["records"],
    "properties": {"records": {"type": "array", "items": {
        "type": "object", "required": ["supplier_sku", "fields"],
        "properties": {
            "supplier_sku": {"type": "string"}, "product_group": {"type": "string"},
            "fields": {"type": "object", "additionalProperties": {
                "type": "object", "required": ["value", "status"],
                "properties": {"value": {}, "unit": {"type": ["string", "null"]}, "system": {"type": ["string", "null"]},
                               "status": {"enum": sorted(ALL_STATUSES)},
                               "evidence": {"type": ["string", "null"]}, "confidence": {"type": ["number", "null"]}}}},
            "issues": {"type": "array", "items": {"type": "object", "required": ["code"],
                       "properties": {"code": {"type": "string"}, "field": {"type": ["string", "null"]}}}}}}}}}

def extract_json(text):
    """Ambil objek JSON pertama dari teks (toleran terhadap pagar kode dan teks tambahan)."""
    if text is None: return None, 'respons kosong'
    t = text.strip()
    if not t: return None, 'respons kosong'
    t = re.sub(r'^```(?:json)?\s*|\s*```$', '', t, flags=re.S).strip()
    try:
        return json.loads(t), None
    except Exception:
        pass
    a, b = t.find('{'), t.rfind('}')
    if a >= 0 and b > a:
        try:
            return json.loads(t[a:b + 1]), None
        except Exception as e:
            return None, f'JSON tidak valid: {e}'
    return None, 'tidak ada objek JSON'

def validate(obj):
    """Kembalikan (records_by_sku, errors). Record tetap dipakai walau ada pelanggaran kecil."""
    errs, recs = [], {}
    if not isinstance(obj, dict) or not isinstance(obj.get('records'), list):
        return {}, ['top-level harus objek dengan daftar "records"']
    for i, r in enumerate(obj['records']):
        if not isinstance(r, dict) or not isinstance(r.get('supplier_sku'), str) or not isinstance(r.get('fields'), dict):
            errs.append(f'record {i}: supplier_sku/fields tidak valid'); continue
        for fn, fe in r['fields'].items():
            if not isinstance(fe, dict) or 'value' not in fe:
                errs.append(f'{r["supplier_sku"]}.{fn}: entri harus objek dengan "value"')
                r['fields'][fn] = {'value': fe if not isinstance(fe, dict) else None, 'status': 'extracted'}; continue
            if fe.get('status') not in ALL_STATUSES:
                errs.append(f'{r["supplier_sku"]}.{fn}: status tidak valid {fe.get("status")!r}')
                fe['status'] = 'extracted' if fe.get('value') is not None else 'missing'
        iss = r.get('issues', [])
        if not isinstance(iss, list): errs.append(f'{r["supplier_sku"]}: issues bukan daftar'); r['issues'] = []
        else: r['issues'] = [x for x in iss if isinstance(x, dict) and 'code' in x]
        recs[norm_key(r['supplier_sku'])] = r
    return recs, errs

def parse_and_validate(text):
    obj, err = extract_json(text)
    if obj is None: return {}, [err], False
    recs, errs = validate(obj)
    return recs, errs, (len(errs) == 0)
