"""Fixed-directory, bounded-memory uploads. No request-selected paths or secret logging."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

CHUNK = 2 * 1024 * 1024
APK_BASE = 'https://ha.msdev.bid/local/apk/'

class Fault(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status

def atomic_json(path, value):
    tmp = path.with_suffix('.json.tmp')
    with tmp.open('w') as file:
        json.dump(value, file, ensure_ascii=False)
        file.flush()
        os.fsync(file.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)

def apk_metadata(path):
    # aapt reads the binary Android manifest, not a client-supplied filename.
    result = subprocess.run(['aapt', 'dump', 'badging', str(path)], capture_output=True, timeout=120)
    match = re.search(r"package: name='([^']+)' versionCode='(\d+)' versionName='([^']*)'", result.stdout.decode(errors='replace'))
    if result.returncode or not match:
        raise Fault('APK-filen saknar ett giltigt Android-manifest.')
    signed = subprocess.run(['apksigner', 'verify', str(path)], capture_output=True, timeout=120)
    if signed.returncode: raise Fault('APK-filens signatur är ogiltig.')
    return match[1], int(match[2]), match[3]

class Store:
    def __init__(self, root, package, inspector=apk_metadata):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # Unfinished APKs and owner metadata must not be served by HA /local/.
        self.parts = self.root.parent.parent / '.rm-apk-uploads'
        self.parts.mkdir(mode=0o700, exist_ok=True)
        self.package = package
        self.inspector = inspector

    def metadata(self, ident, user):
        try: uuid.UUID(ident)
        except Exception: raise Fault('Ogiltig uppladdning.') from None
        try: value = json.loads((self.parts / (ident + '.json')).read_text())
        except Exception: raise Fault('Uppladdningen finns inte.', 404) from None
        if value['user'] != user: raise Fault('Uppladdningen tillhör ett annat konto.', 403)
        return value

    def space(self):
        free = shutil.disk_usage(self.root).free
        reserved = 0
        for path in self.parts.glob('*.json'):
            m = json.loads(path.read_text())
            if m['state'] == 'uploading':
                partial = self.parts / (m['id'] + '.part')
                reserved += max(0, m['size'] - (partial.stat().st_size if partial.exists() else 0))
        return max(0, free - reserved)

    def create(self, user, data):
        if set(data) != {'size', 'sha256', 'versionCode', 'versionName', 'message'}: raise Fault('Ogiltiga metadata.')
        if type(data['size']) is not int or not 0 < data['size'] <= 2**53-1: raise Fault('Ogiltig filstorlek.')
        if not isinstance(data['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', data['sha256']): raise Fault('Ogiltig kontrollsumma.')
        if type(data['versionCode']) is not int or not 0 < data['versionCode'] <= 2**31-1: raise Fault('Ogiltig versionskod.')
        if not isinstance(data['versionName'], str) or not 0 < len(data['versionName']) <= 100: raise Fault('Ogiltigt versionsnamn.')
        if not isinstance(data['message'], str) or len(data['message']) > 2000: raise Fault('Ogiltiga versionsanteckningar.')
        # A selected file can resume after reload; no bearer or secret is persisted.
        for path in self.parts.glob('*.json'):
            m = json.loads(path.read_text())
            if m['user'] == user and all(m[k] == data[k] for k in data):
                if m['state'] == 'uploading': return self.status(m)
                manifest = self.root / 'app-update.json'
                if manifest.exists():
                    current = json.loads(manifest.read_text())
                    if (current.get('apkUrl') == APK_BASE + 'rmlarm-' + m['sha256'] + '.apk'
                            and all(current.get(k) == data[k] for k in ('versionCode', 'versionName', 'message'))): return self.status(m)
        if data['size'] > self.space(): raise Fault('Otillräckligt diskutrymme på Home Assistant.', 507)
        ident = str(uuid.uuid4())
        m = dict(data, id=ident, user=user, state='uploading')
        (self.parts / (ident + '.part')).touch(mode=0o600)
        atomic_json(self.parts / (ident + '.json'), m)
        return self.status(m)

    def status(self, m):
        partial = self.parts / (m['id'] + '.part')
        return dict(id=m['id'], offset=partial.stat().st_size if partial.exists() else m['size'], size=m['size'], state=m['state'], chunkBytes=CHUNK)

    def append(self, m, offset, data):
        if m['state'] != 'uploading': raise Fault('Uppladdningen är redan publicerad.', 409)
        path = self.parts / (m['id'] + '.part')
        if offset != path.stat().st_size: raise Fault('Bytepositionen har ändrats. Återuppta uppladdningen.', 409)
        if not data or len(data) > CHUNK or offset + len(data) > m['size']: raise Fault('Ogiltig fildel.')
        if len(data) > shutil.disk_usage(self.root).free: raise Fault('Otillräckligt diskutrymme på Home Assistant.', 507)
        with path.open('ab') as file:
            file.write(data); file.flush(); os.fsync(file.fileno())
        return self.status(m)

    def finish(self, m):
        if m['state'] == 'complete': return self.status(m)
        path = self.parts / (m['id'] + '.part')
        if path.stat().st_size != m['size']: raise Fault('APK-filen är inte fullständigt uppladdad.', 409)
        with path.open('rb') as file: actual = hashlib.file_digest(file, 'sha256').hexdigest()
        if actual != m['sha256']: raise Fault('Kontrollsumman stämmer inte. Välj rätt fil och försök igen.')
        package, code, name = self.inspector(path)
        if (package, code, name) != (self.package, m['versionCode'], m['versionName']): raise Fault('APK:ns paketnamn eller version stämmer inte med metadata.')
        manifest = self.root / 'app-update.json'
        stable = self.root / 'rmlarm.apk'
        # Preserve the previous manifest/APK pair across crashes between the two renames.
        if manifest.exists():
            old = json.loads(manifest.read_text())
            if old.get('apkUrl') == APK_BASE + 'rmlarm.apk' and stable.exists():
                with stable.open('rb') as file: old_hash = hashlib.file_digest(file, 'sha256').hexdigest()
                old_name = 'rmlarm-' + old_hash + '.apk'
                if not (self.root / old_name).exists(): os.link(stable, self.root / old_name)
                atomic_json(manifest, dict(old, apkUrl=APK_BASE + old_name))
        name = 'rmlarm-' + m['sha256'] + '.apk'
        immutable = self.root / name
        # Hard links do not duplicate the APK or consume a second file-size allocation.
        if not immutable.exists(): os.link(path, immutable)
        temporary = self.root / '.rmlarm.next'
        temporary.unlink(missing_ok=True)
        os.link(immutable, temporary)
        os.replace(temporary, stable)
        atomic_json(manifest, dict(versionCode=m['versionCode'], versionName=m['versionName'], message=m['message'], apkUrl=APK_BASE + name))
        m['state'] = 'complete'
        atomic_json(self.parts / (m['id'] + '.json'), m)
        path.unlink(missing_ok=True)
        return self.status(m)
