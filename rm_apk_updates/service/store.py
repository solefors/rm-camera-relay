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
EXPECTED_PACKAGE = 'com.example.rmdckentreprenadab'
# Plain names only: no path separators, hidden files or the managed update manifest.
FILE_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
RESERVED = {'app-update.json'}

def valid_file_name(name):
    # Versioned rmlarm-<sha256>.apk files back published manifests and must stay immutable.
    if not isinstance(name, str) or not FILE_NAME.fullmatch(name) or name in RESERVED or name.endswith(('.part', '.tmp')) or re.fullmatch(r'rmlarm-[a-f0-9]{64}\.apk', name):
        raise Fault('Ogiltigt filnamn. Använd bokstäver, siffror, punkt, bindestreck eller understreck. app-update.json hanteras av publiceringen.')
    return name

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
        if not isinstance(data, dict) or not {'size', 'sha256'} <= set(data) or set(data) - {'size', 'sha256', 'versionCode', 'versionName', 'message'}: raise Fault('Ogiltiga metadata.')
        if ('versionCode' in data) != ('versionName' in data): raise Fault('Ogiltiga metadata.')
        if type(data['size']) is not int or not 0 < data['size'] <= 2**53-1: raise Fault('Ogiltig filstorlek.')
        if not isinstance(data['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', data['sha256']): raise Fault('Ogiltig kontrollsumma.')
        if 'versionCode' in data and (type(data['versionCode']) is not int or not 0 < data['versionCode'] <= 2**31-1): raise Fault('Ogiltig versionskod.')
        if 'versionName' in data and (not isinstance(data['versionName'], str) or not 0 < len(data['versionName']) <= 100): raise Fault('Ogiltigt versionsnamn.')
        if 'message' in data and (not isinstance(data['message'], str) or len(data['message']) > 2000): raise Fault('Ogiltiga versionsanteckningar.')
        # A selected file can resume after reload; no bearer or secret is persisted.
        for path in self.parts.glob('*.json'):
            m = json.loads(path.read_text())
            if m.get('kind') != 'file' and m['user'] == user and all(m.get(k) == data[k] for k in data):
                if m['state'] == 'uploading': return self.status(m)
                manifest = self.root / 'app-update.json'
                if manifest.exists():
                    current = json.loads(manifest.read_text())
                    versioned = self.root / ('rmlarm-' + m['sha256'] + '.apk')
                    if (current.get('apkUrl') == APK_BASE + versioned.name
                            and all(current.get(k) == m.get(k) for k in ('versionCode', 'versionName'))
                            and ('message' not in data or current.get('message') == data['message'])
                            and versioned.exists()):
                        with versioned.open('rb') as file:
                            if hashlib.file_digest(file, 'sha256').hexdigest() == m['sha256']: return self.status(m)
        if data['size'] > self.space(): raise Fault('Otillräckligt diskutrymme på Home Assistant.', 507)
        ident = str(uuid.uuid4())
        m = dict(data, id=ident, user=user, state='uploading')
        (self.parts / (ident + '.part')).touch(mode=0o600)
        atomic_json(self.parts / (ident + '.json'), m)
        return self.status(m)

    def create_file(self, user, data):
        if not isinstance(data, dict) or set(data) != {'name', 'size', 'sha256'}: raise Fault('Ogiltiga metadata.')
        name = valid_file_name(data['name'])
        if type(data['size']) is not int or not 0 < data['size'] <= 2**53-1: raise Fault('Ogiltig filstorlek.')
        if not isinstance(data['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', data['sha256']): raise Fault('Ogiltig kontrollsumma.')
        for path in self.parts.glob('*.json'):
            m = json.loads(path.read_text())
            if m.get('kind') == 'file' and m['user'] == user and m['state'] == 'uploading' and all(m[k] == data[k] for k in data): return self.status(m)
        if data['size'] > self.space(): raise Fault('Otillräckligt diskutrymme på Home Assistant.', 507)
        ident = str(uuid.uuid4())
        m = dict(data, name=name, id=ident, user=user, state='uploading', kind='file')
        (self.parts / (ident + '.part')).touch(mode=0o600)
        atomic_json(self.parts / (ident + '.json'), m)
        return self.status(m)

    def finish_file(self, m):
        # APKs may never bypass manifest/signature checks through the generic file picker.
        if m['name'].lower().endswith('.apk'): return self.finish(m)
        if m['state'] == 'complete': return self.status(m)
        path = self.parts / (m['id'] + '.part')
        if path.stat().st_size != m['size']: raise Fault('Filen är inte fullständigt uppladdad.', 409)
        with path.open('rb') as file: actual = hashlib.file_digest(file, 'sha256').hexdigest()
        if actual != m['sha256']: raise Fault('Kontrollsumman stämmer inte. Välj rätt fil och försök igen.')
        target = self.root / valid_file_name(m['name'])
        os.chmod(path, 0o644)
        # Same filesystem: one rename replaces any existing file atomically; readers see old or new, never partial.
        os.replace(path, target)
        fd = os.open(self.root, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
        m['state'] = 'complete'
        atomic_json(self.parts / (m['id'] + '.json'), m)
        return self.status(m)

    def status(self, m):
        partial = self.parts / (m['id'] + '.part')
        result = dict(id=m['id'], offset=partial.stat().st_size if partial.exists() else m['size'], size=m['size'], state=m['state'], chunkBytes=CHUNK)
        if m['state'] == 'complete' and 'publication' in m: result['publication'] = m['publication']
        return result

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
        if m.get('kind') == 'file' and not m['name'].lower().endswith('.apk'): raise Fault('Fel uppladdningstyp.', 409)
        if m['state'] == 'complete': return self.status(m)
        path = self.parts / (m['id'] + '.part')
        if path.stat().st_size != m['size']: raise Fault('APK-filen är inte fullständigt uppladdad.', 409)
        with path.open('rb') as file: actual = hashlib.file_digest(file, 'sha256').hexdigest()
        if actual != m['sha256']: raise Fault('Kontrollsumman stämmer inte. Välj rätt fil och försök igen.')
        package, code, name = self.inspector(path)
        if package != EXPECTED_PACKAGE or package != self.package: raise Fault('APK:ns paketnamn måste vara ' + EXPECTED_PACKAGE + '.')
        if type(code) is not int or not 0 < code <= 2**31-1 or not isinstance(name, str) or not 0 < len(name.strip()) <= 100: raise Fault('APK:ns versionsuppgifter är ogiltiga.')
        if 'versionCode' in m and (code, name) != (m['versionCode'], m['versionName']): raise Fault('APK:ns paketnamn eller version stämmer inte med metadata.')
        manifest = self.root / 'app-update.json'
        stable = self.root / 'rmlarm.apk'
        # HA static files are cached for 31 days; no-cache request headers do not
        # invalidate Cloudflare's cached stable URL. Bind each manifest to exact bytes.
        versioned = self.root / ('rmlarm-' + actual + '.apk')
        if versioned.exists():
            with versioned.open('rb') as file:
                if hashlib.file_digest(file, 'sha256').hexdigest() != actual:
                    raise Fault('Den oföränderliga versionsfilens kontrollsumma är fel. Publiceringen stoppades.', 409)
        old = {}
        if manifest.exists():
            try: old = json.loads(manifest.read_text())
            except (ValueError, OSError): raise Fault('Nuvarande app-update.json kunde inte läsas. Publicerade filer är oförändrade.') from None
            if not isinstance(old, dict): raise Fault('Nuvarande app-update.json har ogiltigt format. Publicerade filer är oförändrade.')
        release = dict(old, versionCode=code, versionName=name, apkUrl=APK_BASE + versioned.name)
        if 'packageName' in old: release['packageName'] = package
        if 'message' in m: release['message'] = m['message']
        elif 'message' not in release: release['message'] = ''
        # Prepare the entire JSON privately before touching either published file.
        prepared = self.parts / (m['id'] + '.release.json')
        atomic_json(prepared, release)
        backups = []
        for target in (stable, manifest):
            backup = self.parts / (m['id'] + '.' + target.name + '.backup')
            backup.unlink(missing_ok=True)
            if target.exists(): os.link(target, backup)
            backups.append((target, backup))
        temporary = self.parts / (m['id'] + '.next.apk')
        temporary.unlink(missing_ok=True)
        os.chmod(path, 0o644)
        os.link(path, temporary)
        created_versioned = False
        try:
            if not versioned.exists():
                os.link(path, versioned)
                created_versioned = True
            os.replace(temporary, stable)
            os.replace(prepared, manifest)
            fd = os.open(self.root, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        except OSError:
            for target, backup in backups:
                if backup.exists(): os.replace(backup, target)
                else: target.unlink(missing_ok=True)
            if created_versioned: versioned.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)
            raise Fault('Publiceringen misslyckades. Föregående APK och app-update.json har återställts.', 503) from None
        for _, backup in backups: backup.unlink(missing_ok=True)
        m.update(versionCode=code, versionName=name, publication=dict(packageName=package, versionCode=code, versionName=name, apkUrl=release['apkUrl'], manifestUrl=APK_BASE + 'app-update.json'))
        m['state'] = 'complete'
        atomic_json(self.parts / (m['id'] + '.json'), m)
        path.unlink(missing_ok=True)
        return self.status(m)
