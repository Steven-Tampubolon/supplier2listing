"""Baseline aturan murni (tanpa LLM) untuk T1. Sengaja generik: header baku Indonesia/Inggris + pencocokan fuzzy.
Tidak menangani varian-dalam-satu-sel (V1), tidak menormalkan kategori, tidak membaca PDF/gambar."""
import difflib, json, re
from .common import norm_key

SYN = {
    'parent': ['kode induk', 'style code', 'parent', 'parent id'],
    'sku': ['sku', 'kode sku', 'item no', 'item#', 'kode barang', 'article no'],
    'name': ['nama produk', 'product name', 'nama', 'item name', 'title'],
    'brand': ['merek', 'brand', 'manufacturer', 'mfr'],
    'category': ['kategori', 'category'],
    'color': ['warna', 'color', 'colour'], 'size': ['ukuran', 'size'], 'material': ['bahan', 'material', 'fabric'],
    'specs': ['spesifikasi', 'specifications', 'specs'], 'gtin': ['barcode', 'ean', 'gtin', 'upc', 'ean/upc', 'barcode ean', 'ean13'],
    'mpn': ['mpn', 'part no', 'model no', 'kode pabrik'], 'variant': ['varian', 'variant', 'option'],
    'cost': ['harga modal', 'cost price', 'harga', 'price'], 'stock': ['stok', 'stock', 'qty', 'quantity'],
    'weight': ['berat', 'weight'], 'weight_item': ['berat produk', 'item weight', 'net weight'],
    'weight_pkg': ['berat kemasan', 'package weight', 'gross weight'],
    'dims': ['dimensi', 'dimensions', 'dimensi p x l x t', 'dimensions l x w x h'], 'warranty': ['garansi', 'warranty'],
    'volume': ['isi bersih', 'net content', 'volume'], 'ingredients': ['komposisi', 'ingredients', 'inci'],
    'description': ['deskripsi', 'description', 'keterangan'], 'image': ['foto', 'image', 'gambar'],
}
_ALL = [(s, k) for k, v in SYN.items() for s in v]
COLOR_WORDS = {'hitam': 'black', 'black': 'black', 'putih': 'white', 'white': 'white', 'navy': 'navy', 'abu-abu': 'grey', 'grey': 'grey', 'gray': 'grey',
               'krem': 'beige', 'beige': 'beige', 'hijau olive': 'olive', 'olive': 'olive', 'marun': 'maroon', 'maroon': 'maroon', 'merah': 'red', 'red': 'red',
               'biru': 'blue', 'blue': 'blue', 'merah muda': 'pink', 'pink': 'pink', 'coklat': 'brown', 'brown': 'brown', 'perak': 'silver', 'silver': 'silver'}
REQUIRED = {'fashion': ['material', 'color', 'size'], 'electronics': ['warranty'], 'beauty': ['ingredients', 'net_content']}

def _hnorm(h):
    h = re.sub(r'\(.*?\)', '', h or ''); h = re.sub(r'[^\w ]+', ' ', h.casefold()); return re.sub(r'\s+', ' ', h).strip()

def map_header(h):
    n = _hnorm(h)
    if not n: return None
    for s, k in _ALL:
        if _hnorm(s) == n: return k
    m = difflib.get_close_matches(n, [_hnorm(s) for s, _ in _ALL], n=1, cutoff=0.82)
    if m:
        for s, k in _ALL:
            if _hnorm(s) == m[0]: return k
    return None

def _num(s):
    m = re.search(r'[-+]?\d+(?:[.,]\d+)?', str(s)); return float(m.group().replace(',', '.')) if m else None

def parse_price(txt, header):
    t = str(txt).strip(); low = t.casefold()
    cur = 'IDR' if ('rp' in low or 'idr' in low) else 'USD' if ('$' in low or 'usd' in low) else 'EUR' if ('€' in low or 'eur' in low) else None
    if cur is None:
        m = re.search(r'\((\w{3})\)', header or ''); cur = m.group(1).upper() if m else None
    digits = re.sub(r'[^\d.,]', '', t)
    if not digits or cur is None: return None, cur
    if cur == 'IDR': return int(re.sub(r'[.,]', '', digits)), cur
    if cur == 'EUR': return float(digits.replace('.', '').replace(',', '.')), cur
    return float(digits.replace(',', '')), cur

def parse_qty(txt):
    m = re.match(r'\s*([-+]?\d+(?:[.,]\d+)?)\s*([A-Za-z. ]*)', str(txt))
    return (float(m.group(1).replace(',', '.')), m.group(2).strip() or None) if m else (None, None)

def parse_size(raw):
    t = re.sub(r'(?i)\b(size|ukuran|eu)\b|[()"\']', '', raw).strip()
    t = {'small': 'S', 'medium': 'M', 'large': 'L', 'x-large': 'XL'}.get(t.casefold(), t)
    t = re.sub(r'^[Ww](?=\d)', '', t)
    if re.fullmatch(r'(?i)all size', t): return 'All Size', 'one_size'
    if re.fullmatch(r'\d+ pcs', t): return t, 'pack'
    if t.upper() in ('S', 'M', 'L', 'XL'): return t.upper(), 'alpha'
    if t.isdigit(): return t, ('EU' if int(t) >= 37 else 'waist_inch')
    return t, None

def gtin_ok(g):
    if not g.isdigit() or len(g) != 13: return False
    s = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(g[:-1])))
    return str((10 - s % 10) % 10) == g[-1]

def predict(unit, category_hint=None):
    """Kembalikan teks JSON keluaran (format kontrak) dari unit T1 bertipe tabel."""
    g = unit.get('grid')
    if not g: return json.dumps({'records': []})
    rows = {int(k): v for k, v in g['rows'].items()}
    best = (0, None, None)
    for rn, cells in sorted(rows.items()):
        m = [map_header(c) for c in cells]; n = sum(1 for x in m if x)
        if n > best[0]: best = (n, rn, m)
    if best[0] < 3: return json.dumps({'records': []})
    hdr_row, cmap = best[1], best[2]; heads = rows[hdr_row]
    col = {}
    for i, k in enumerate(cmap):
        if k and k not in col: col[k] = i
    recs, seen = {}, {}
    for rn in sorted(rows):
        if rn <= hdr_row: continue
        c = rows[rn]
        get = lambda k: (c[col[k]].strip() if k in col and col[k] < len(c) else '')
        sku = get('sku')
        if not sku or ';' in sku: continue
        if sku in recs:
            recs[sku]['issues'].append(dict(code='DUPLICATE_SKU', field='supplier_sku')); continue
        f, iss = {}, []
        def put(name, value, ev, unit=None, system=None, status='extracted'):
            e = dict(value=value, status=status, evidence=ev or None, confidence=None)
            if unit: e['unit'] = unit
            if system: e['system'] = system
            f[name] = e
        for k, fname in (('name', 'name'), ('brand', 'brand'), ('mpn', 'mpn')):
            if get(k): put(fname, get(k), get(k))
        put('category', None, None, status='missing')
        v = get('gtin')
        if not v: put('gtin', None, None, status='missing'); iss.append(dict(code='GTIN_MISSING', field='gtin'))
        elif not gtin_ok(re.sub(r'\D', '', v)): put('gtin', v, v, status='error'); iss.append(dict(code='GTIN_INVALID', field='gtin'))
        else: put('gtin', v, v)
        if get('cost'):
            val, cur = parse_price(get('cost'), heads[col['cost']])
            if val is not None: put('cost_price', val, get('cost'), unit=cur)
        if get('stock') and _num(get('stock')) is not None: put('stock', int(_num(get('stock'))), get('stock'))
        w = get('weight_item') or get('weight')
        if w:
            n, u = parse_qty(w)
            if n is not None: put('weight_item', n, w, unit=u or 'g')
        if get('weight_pkg'):
            n, u = parse_qty(get('weight_pkg'))
            if n is not None: put('weight_package', n, get('weight_pkg'), unit=u or 'g')
        if get('dims'):
            nums = [float(x.replace(',', '.')) for x in re.findall(r'\d+(?:[.,]\d+)?', get('dims'))]
            if len(nums) == 3: put('dims_item', nums, get('dims'), unit='cm')
        if get('warranty'):
            n, u = parse_qty(get('warranty'))
            if n is not None: put('warranty', n, get('warranty'), unit=u)
        if get('material'): put('material', get('material'), get('material'))
        if get('ingredients'): put('ingredients', get('ingredients'), get('ingredients'))
        if get('color'):
            cl = COLOR_WORDS.get(get('color').casefold())
            if cl: put('color', cl, get('color'))
        if get('size'):
            sv, sy = parse_size(get('size')); put('size', sv, get('size'), system=sy)
        if get('volume'):
            n, u = parse_qty(get('volume'))
            if n is not None: put('net_content', n, get('volume'), unit=u)
        if get('variant') and 'color' not in col:
            vt = get('variant'); cl = COLOR_WORDS.get(vt.casefold())
            if cl: put('color', cl, vt)
            elif re.fullmatch(r'\d+(\.\d+)? ?(GB|TB|mAh)', vt, re.I):
                n, u = parse_qty(vt); put('capacity', n, vt, unit=u)
            elif re.fullmatch(r'\d+(\.\d+)? ?m', vt, re.I): n, u = parse_qty(vt); put('length', n, vt, unit='m')
        if get('specs'):
            for seg in get('specs').split(';'):
                if ':' not in seg: continue
                k, val = (x.strip() for x in seg.split(':', 1))
                kl = k.casefold()
                if kl in ('bahan', 'material'): put('material', val, seg.strip())
                elif kl in ('warna', 'color'):
                    cl = COLOR_WORDS.get(val.casefold())
                    if cl: put('color', cl, seg.strip())
                elif kl in ('ukuran', 'size'): sv, sy = parse_size(val); put('size', sv, seg.strip(), system=sy)
                else: put('spec:' + k, val, seg.strip())
        for fld in REQUIRED.get(category_hint or '', []):
            if fld not in f and (fld in col or fld == 'net_content' and 'volume' in col):
                iss.append(dict(code='REQUIRED_ATTR_MISSING', field=fld))
        recs[sku] = dict(supplier_sku=sku, product_group=get('parent') or f'g-{sku}', fields=f, issues=iss)
    return json.dumps({'records': list(recs.values())}, ensure_ascii=False)
