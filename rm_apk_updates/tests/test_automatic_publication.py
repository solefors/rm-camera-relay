import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from service.store import Store, Fault, APK_BASE, EXPECTED_PACKAGE, atomic_json


class AutomaticPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'www' / 'apk', EXPECTED_PACKAGE,
                           lambda p: (EXPECTED_PACKAGE, 4, '1.0.3'))
        self.body = b'new-verified-apk'
        self.old = dict(versionCode=3, versionName='1.0.2', message='Behåll anteckningar',
                        apkUrl=APK_BASE + 'rmlarm.apk', custom={'enabled': True})
        self.store.root.joinpath('rmlarm.apk').write_bytes(b'old-apk')
        atomic_json(self.store.root / 'app-update.json', self.old)
        self.old_json = self.store.root.joinpath('app-update.json').read_bytes()

    def tearDown(self):
        self.tmp.cleanup()

    def upload(self, generic=False, complete=True):
        data = dict(size=len(self.body), sha256=hashlib.sha256(self.body).hexdigest())
        state = (self.store.create_file('admin', dict(data, name='release-1.0.3.apk'))
                 if generic else self.store.create('admin', data))
        m = self.store.metadata(state['id'], 'admin')
        self.store.append(m, 0, self.body if complete else self.body[:3])
        return m

    def assert_old(self):
        self.assertEqual(self.store.root.joinpath('rmlarm.apk').read_bytes(), b'old-apk')
        self.assertEqual(self.store.root.joinpath('app-update.json').read_bytes(), self.old_json)

    def test_reads_actual_versions_without_client_metadata(self):
        result = self.store.finish(self.upload())
        self.assertEqual(result['publication']['packageName'], 'com.example.rmdckentreprenadab')
        self.assertEqual(result['publication']['versionCode'], 4)
        self.assertEqual(result['publication']['versionName'], '1.0.3')
        self.assertEqual(result['publication']['apkUrl'], 'https://ha.msdev.bid/local/apk/rmlarm.apk')
        manifest = json.loads(self.store.root.joinpath('app-update.json').read_text())
        self.assertEqual(manifest, dict(self.old, versionCode=4, versionName='1.0.3'))
        self.assertEqual(self.store.root.joinpath('rmlarm.apk').read_bytes(), self.body)
        self.assertEqual(Store(self.store.root, EXPECTED_PACKAGE).status(
            self.store.metadata(result['id'], 'admin'))['publication'], result['publication'])

    def test_generic_apk_uses_same_verified_publication(self):
        result = self.store.finish_file(self.upload(generic=True))
        self.assertEqual(result['publication']['versionCode'], 4)
        self.assertFalse(self.store.root.joinpath('release-1.0.3.apk').exists())
        self.assertEqual(json.loads(self.store.root.joinpath('app-update.json').read_text())['versionCode'], 4)

    def test_wrong_package_keeps_both_published_files(self):
        m = self.upload(generic=True)
        self.store.inspector = lambda p: ('evil.package', 4, '1.0.3')
        with self.assertRaises(Fault): self.store.finish_file(m)
        self.assert_old()

    def test_invalid_versions_keep_both_published_files(self):
        m = self.upload()
        for code, name in ((0, '1.0.3'), (True, '1.0.3'), (4, ''), (4, '   '), (4, None)):
            with self.subTest(code=code, name=name):
                self.store.inspector = lambda p: (EXPECTED_PACKAGE, code, name)
                with self.assertRaises(Fault): self.store.finish(m)
                self.assert_old()

    def test_invalid_signature_keeps_both_published_files(self):
        m = self.upload()
        def reject(path): raise Fault('APK-filens signatur är ogiltig.')
        self.store.inspector = reject
        with self.assertRaises(Fault): self.store.finish(m)
        self.assert_old()

    def test_incomplete_upload_does_not_update_manifest(self):
        with self.assertRaises(Fault): self.store.finish(self.upload(complete=False))
        self.assert_old()

    def test_bad_hash_keeps_both_published_files(self):
        m = self.upload(); m['sha256'] = '0' * 64
        with self.assertRaises(Fault): self.store.finish(m)
        self.assert_old()

    def test_legacy_client_version_mismatch_keeps_both_files(self):
        m = self.upload(); m.update(versionCode=5, versionName='1.0.4')
        with self.assertRaises(Fault): self.store.finish(m)
        self.assert_old()

    def test_manifest_is_replaced_only_after_complete_apk(self):
        import os
        replace = os.replace
        observed = []
        def inspect(source, target):
            if Path(target) == self.store.root / 'app-update.json':
                self.assertEqual(self.store.root.joinpath('rmlarm.apk').read_bytes(), self.body)
                self.assertEqual(self.store.root.joinpath('app-update.json').read_bytes(), self.old_json)
                self.assertEqual(json.loads(Path(source).read_text())['versionCode'], 4)
                observed.append(True)
            return replace(source, target)
        m = self.upload()
        with patch('service.store.os.replace', side_effect=inspect): self.store.finish(m)
        self.assertEqual(observed, [True])

    def test_json_write_failure_rolls_back_apk(self):
        import os
        replace = os.replace
        def fail(source, target):
            if str(source).endswith('.release.json'): raise OSError('disk error')
            return replace(source, target)
        m = self.upload()
        with patch('service.store.os.replace', side_effect=fail):
            with self.assertRaises(Fault): self.store.finish(m)
        self.assert_old()
        self.assertEqual(self.store.finish(m)['state'], 'complete')

    def test_bad_existing_manifest_is_not_overwritten(self):
        self.store.root.joinpath('app-update.json').write_text('{invalid')
        self.old_json = b'{invalid'
        with self.assertRaises(Fault): self.store.finish(self.upload())
        self.assert_old()