#!/usr/bin/env python3
"""Build the bookseller's shop website from the bookseller edition's database.

  python3 tools-shop/build.py --books DIR --rooms DIR --settings FILE --photos photos.json --out OUT

books DIR:   one JSON file per book (ArtifactData list out_dir of the "books" collection)
rooms DIR:   one JSON file per viewing room ("rooms" collection); may be missing
settings:    meta/settings.json from the bookseller database; may be missing
photos.json: {"<photo id>": "<asset url in the shop artifact>"} for photos already copied
out OUT:     receives index.html, data.json and rooms/<token>.json

Only books in stock, not hidden, and offered on "Own website" are published. Cost, consignor, notes,
customers and every other private field stay out. Prints JSON: books, rooms, needed_photo_ids, removed_rooms.
"""
import argparse, json, os, glob, re, shutil, datetime

SIG = {"Signed by the author": "Signed", "Inscribed by the author": "Inscribed", "Signed by the illustrator": "Signed by illustrator",
       "Inscribed by the illustrator": "Inscribed by illustrator", "Signed by the author and the illustrator": "Signed by author and illustrator",
       "Association copy": "Association copy"}

PRIVATE_LABEL = re.compile(r'invoice|receipt|purchase|bill of sale|packing slip|shipping', re.I)  # dealer papers never go on the website

def load_dir(d):
    out = {}
    for f in sorted(glob.glob(os.path.join(d or '', '*.json'))):
        try: out[os.path.basename(f)[:-5]] = json.load(open(f))
        except Exception: pass
    return out

def num(v):
    try: return float(str(v).replace('$', '').replace(',', '').strip())
    except Exception: return 0.0

def in_stock(b): return b.get('stockStatus') not in ('Sold', 'Returned to consignor')

def on_site(b):
    return in_stock(b) and not b.get('hidden') and any(l.get('where') == 'Own website' and not l.get('down') for l in (b.get('listings') or []))

def year_of(b):
    y = b.get('year')
    try: return int(y)
    except Exception:
        m = re.search(r'\b(1[4-9]\d\d|20\d\d)\b', str(b.get('date') or ''))
        return int(m.group(1)) if m else None

def describe(b):
    if (b.get('catalogueDesc') or '').strip(): return b['catalogueDesc'].strip()
    j = lambda *xs: ', '.join(x for x in xs if x)
    parts = [j(b.get('format'), b.get('pages')), b.get('editionStatus'), b.get('issueState'), b.get('illustrations'),
             j(b.get('bindingType'), b.get('bindingMaterial')),
             ('Condition: ' + b['conditionBook'] + ('. ' + b['conditionNotes'] if b.get('conditionNotes') else '')) if b.get('conditionBook') else '',
             ('Dust jacket: ' + j(b.get('jacket'), b.get('conditionJacket'))) if b.get('jacket') and b.get('jacket') != 'None issued' else '',
             (b['signed'] + (' to ' + b['inscribedTo'] if b.get('inscribedTo') else '')) if SIG.get(b.get('signed')) else '',
             ('Provenance: ' + b['provenance']) if b.get('provenance') else '',
             ('References: ' + b['reference']) if b.get('reference') else '']
    parts = [re.sub(r'\.\s*$', '', str(x).strip()) for x in parts if x and str(x).strip()]
    return '. '.join(parts) + ('.' if parts else '')

def public(b, photos, need, price=None):
    ph = []
    for p in b.get('photos') or []:
        pid = p.get('id')
        if not pid or PRIVATE_LABEL.search(p.get('label') or ''): continue
        if pid in photos: ph.append({'src': photos[pid], 'label': p.get('label') or ''})
        else: need.add(pid)
    imp = ', '.join(x for x in [': '.join(x for x in [b.get('place'), b.get('publisher')] if x), str(b.get('date') or b.get('year') or '')] if x)
    held = b.get('stockStatus') in ('On hold', 'Reserved for a customer', 'On approval with a customer', 'On memo to another dealer')
    return {'no': b.get('stockNo') or '', 'author': b.get('author') or '', 'dates': b.get('authorDates') or '',
            'title': b.get('title') or b.get('shortTitle') or '', 'short': b.get('shortTitle') or '', 'imprint': imp, 'year': year_of(b),
            'edition': b.get('editionStatus') or '', 'category': b.get('category') or '', 'language': b.get('language') or '',
            'condition': b.get('conditionBook') or '', 'signed': SIG.get(b.get('signed'), ''), 'desc': describe(b),
            'price': round(price if price is not None else num(b.get('askingPrice'))), 'reserved': held, 'photos': ph,
            'added': (b.get('listings') and min((l.get('since') or '') for l in b['listings'] if l.get('where') == 'Own website')) or ''}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--books', required=True); ap.add_argument('--rooms'); ap.add_argument('--settings'); ap.add_argument('--photos'); ap.add_argument('--out', required=True)
    a = ap.parse_args()
    books = load_dir(a.books); rooms = load_dir(a.rooms) if a.rooms else {}
    st = json.load(open(a.settings)) if a.settings and os.path.exists(a.settings) else {}
    photos = json.load(open(a.photos)) if a.photos and os.path.exists(a.photos) else {}
    need = set()
    listed = [public(b, photos, need) for b in books.values() if on_site(b)]
    listed.sort(key=lambda x: (re.sub(r'[^a-z]', '', x['author'].lower()), x['title'].lower()))
    shop = {'name': st.get('shopName') or 'Atlas Bibliotheca', 'tagline': st.get('shopTagline') or 'Rare Books & Fine Editions',
            'email': st.get('shopEmail') or '', 'phone': st.get('shopPhone') or '', 'address': st.get('shopAddress') or '',
            'about': st.get('shopAbout') or '', 'terms': st.get('shopTerms') or 'All books are guaranteed as described and may be returned within ten days of receipt. Prices are in US dollars; shipping and any sales tax are added to the invoice.',
            'sample': bool(st.get('shopSample', True))}
    os.makedirs(a.out, exist_ok=True)
    built = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
    json.dump({'shop': shop, 'built': built, 'books': listed}, open(os.path.join(a.out, 'data.json'), 'w'), ensure_ascii=False, separators=(',', ':'))
    rd = os.path.join(a.out, 'rooms'); os.makedirs(rd, exist_ok=True)
    today = datetime.date.today().isoformat(); live = []
    for rid, r in rooms.items():
        tok = r.get('token') or ''
        if not re.fullmatch(r'[a-z0-9]{12,40}', tok): continue
        if (r.get('expires') or '9999') < today or r.get('closed'): continue
        items = []
        for it in r.get('items') or []:
            b = books.get(it.get('id'))
            if b and in_stock(b) and not b.get('hidden'): items.append(public(b, photos, need, num(it.get('price')) or None))
        json.dump({'shop': shop, 'customer': r.get('customer') or '', 'note': r.get('note') or '', 'expires': r.get('expires') or '', 'books': items},
                  open(os.path.join(rd, tok + '.json'), 'w'), ensure_ascii=False, separators=(',', ':'))
        live.append(tok)
    removed = [os.path.basename(f)[:-5] for f in glob.glob(os.path.join(rd, '*.json')) if os.path.basename(f)[:-5] not in live]
    for t in removed: os.remove(os.path.join(rd, t + '.json'))
    shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shop.html'), os.path.join(a.out, 'index.html'))
    print(json.dumps({'books': len(listed), 'rooms': live, 'needed_photo_ids': sorted(need), 'removed_rooms': removed}))

if __name__ == '__main__': main()
