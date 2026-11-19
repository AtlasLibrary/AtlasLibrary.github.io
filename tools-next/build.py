#!/usr/bin/env python3
"""Build the password-protected Atlas Library website.

Usage:
  ATLAS_PW='the password' python3 tools/build.py --books DIR --photos DIR [--photos DIR ...] --site SITE_DIR
  ATLAS_PW='new' ATLAS_OLD_PW='old' python3 tools/build.py --site SITE_DIR --change-password

books DIR: JSON files from the main Atlas Library database collection "books" (one file per book).
photos DIR: folders holding original photos named <photo id>.jpg (only needed for photos not yet on the site).
Everything published (catalogue data and every photo) is encrypted with AES-256-GCM under a random content key;
that key is stored in key.json wrapped with a key derived from the password (PBKDF2-SHA256).
Prints a JSON summary: books, missing_photo_ids, changed.
"""
import argparse, base64, datetime, hashlib, io, json, os, re, sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from PIL import Image, ImageOps

ITER = 310000
TOOLS = os.path.dirname(os.path.abspath(__file__))
INV = re.compile(r'invoice|receipt|bill of sale', re.I)
b64e = lambda b: base64.b64encode(b).decode()
b64d = base64.b64decode


def kek(pw, salt, it):
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=it).derive(pw.encode())


def seal(key, data):
    iv = os.urandom(12)
    return iv + AESGCM(key).encrypt(iv, data, None)


def unseal(key, blob):
    return AESGCM(key).decrypt(blob[:12], blob[12:], None)


def content_key(site, pw):
    path = os.path.join(site, 'key.json')
    if os.path.exists(path):
        kj = json.load(open(path))
        return unseal(kek(pw, b64d(kj['salt']), kj['iter']), b64d(kj['wrapped']))
    ck, salt = os.urandom(32), os.urandom(16)
    json.dump({'salt': b64e(salt), 'iter': ITER, 'wrapped': b64e(seal(kek(pw, salt, ITER), ck))}, open(path, 'w'))
    return ck


def jpeg(src, maxside, q):
    im = ImageOps.exif_transpose(Image.open(src)).convert('RGB')
    im.thumbnail((maxside, maxside), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, 'JPEG', quality=q, optimize=True, progressive=True)
    return out.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--books'); ap.add_argument('--photos', action='append', default=[]); ap.add_argument('--site', required=True)
    ap.add_argument('--change-password', action='store_true')
    a = ap.parse_args()
    pw = os.environ.get('ATLAS_PW')
    if not pw:
        sys.exit('Set ATLAS_PW')
    site = a.site
    if a.change_password:
        old = os.environ['ATLAS_OLD_PW']
        ck = content_key(site, old)
        salt = os.urandom(16)
        json.dump({'salt': b64e(salt), 'iter': ITER, 'wrapped': b64e(seal(kek(pw, salt, ITER), ck))}, open(os.path.join(site, 'key.json'), 'w'))
        print(json.dumps({'password_changed': True})); return
    ck = content_key(site, pw)
    fields = json.load(open(os.path.join(TOOLS, 'fields.json')))
    groups = json.load(open(os.path.join(TOOLS, 'groups.json')))
    os.makedirs(os.path.join(site, 'img', 't'), exist_ok=True)
    books, missing, used = [], [], set()
    skp = os.path.join(TOOLS, 'skip.json')
    skip = set(json.load(open(skp))) if os.path.exists(skp) else set()
    for f in sorted(os.listdir(a.books)):
        if not f.endswith('.json'):
            continue
        d = json.load(open(os.path.join(a.books, f)))
        if d.get('status') not in (None, '', 'In collection', 'On loan'):
            continue
        b = {'id': f[:-5]}
        for k in fields:
            v = d.get(k)
            if v not in (None, '', []):
                b[k] = v
        ph = []
        for p in d.get('photos') or []:
            if INV.search(p.get('label') or ''):
                continue
            pid = p['id']
            if pid in skip:
                continue
            full, th = os.path.join(site, 'img', pid + '.bin'), os.path.join(site, 'img', 't', pid + '.bin')
            if not (os.path.exists(full) and os.path.exists(th)):
                src = next((os.path.join(dd, pid + '.jpg') for dd in a.photos if os.path.exists(os.path.join(dd, pid + '.jpg'))), None)
                if not src:
                    missing.append(pid); continue
                try:
                    Image.open(src).verify()
                except Exception:
                    skip.add(pid); json.dump(sorted(skip), open(skp, 'w'))
                    continue  # not a photo (e.g. a PDF document): never published
                open(full, 'wb').write(seal(ck, jpeg(src, 1600, 82)))
                open(th, 'wb').write(seal(ck, jpeg(src, 360, 78)))
            used.add(pid)
            ph.append({'src': 'img/%s.bin' % pid, 'thumb': 'img/t/%s.bin' % pid, 'label': p.get('label') or ''})
        b['photos'] = ph
        books.append(b)
    if not missing:  # remove photos no longer in the catalogue
        for sub in ('img', os.path.join('img', 't')):
            for fn in os.listdir(os.path.join(site, sub)):
                if fn.endswith('.bin') and fn[:-4] not in used:
                    os.remove(os.path.join(site, sub, fn))
    sig = hashlib.sha256(json.dumps(books, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    stp = os.path.join(site, 'state.json')
    old = json.load(open(stp)) if os.path.exists(stp) else {}
    changed = old.get('sig') != sig
    if changed:
        try:
            from zoneinfo import ZoneInfo
            now = datetime.datetime.now(ZoneInfo('America/Los_Angeles'))
        except Exception:
            now = datetime.datetime.utcnow() - datetime.timedelta(hours=8)
        upd = now.strftime('%B %-d, %Y, %-I:%M %p') + ' Pacific'
        payload = json.dumps({'books': books, 'groups': groups, 'updated': upd}, ensure_ascii=False).encode()
        open(os.path.join(site, 'data.bin'), 'wb').write(seal(ck, payload))
        json.dump({'sig': sig}, open(stp, 'w'))
    tpl = open(os.path.join(TOOLS, 'index.template.html')).read()
    if not os.path.exists(os.path.join(site, 'index.html')) or open(os.path.join(site, 'index.html')).read() != tpl:
        open(os.path.join(site, 'index.html'), 'w').write(tpl); changed = True
    open(os.path.join(site, '.nojekyll'), 'w').write('')
    print(json.dumps({'books': len(books), 'photos': len(used), 'missing_photo_ids': missing, 'changed': changed}))


if __name__ == '__main__':
    main()
