import hashlib, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from service.store import Store, Fault, APK_BASE
class UploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)/'www'/'apk'
        self.store=Store(self.root,'com.example.rmdckentreprenadab',lambda p:('com.example.rmdckentreprenadab',4,'1.0.3'))
        self.bytes=b'complete-test-apk'
        self.data=dict(size=len(self.bytes),sha256=hashlib.sha256(self.bytes).hexdigest(),versionCode=4,versionName='1.0.3',message='Ny version')
    def tearDown(self): self.tmp.cleanup()
    def upload(self):
        s=self.store.create('admin',self.data); m=self.store.metadata(s['id'],'admin'); self.store.append(m,0,self.bytes); return m
    def test_resume_after_restart(self):
        s=self.store.create('admin',self.data); self.store.append(self.store.metadata(s['id'],'admin'),0,self.bytes[:3])
        self.assertEqual(Store(self.root,'com.example.rmdckentreprenadab').create('admin',self.data)['offset'],3)
        with self.assertRaises(Fault): self.store.append(self.store.metadata(s['id'],'admin'),0,b'x')
    def test_owner_only(self):
        s=self.store.create('admin',self.data)
        with self.assertRaises(Fault) as e: self.store.metadata(s['id'],'other')
        self.assertEqual(e.exception.status,403)
    def test_space_and_no_low_limit(self):
        with patch.object(self.store,'space',return_value=0):
            with self.assertRaises(Fault) as e: self.store.create('admin',self.data)
            self.assertEqual(e.exception.status,507)
        with patch.object(self.store,'space',return_value=10*1024**3): self.assertEqual(self.store.create('admin',dict(self.data,size=9*1024**3))['size'],9*1024**3)
    def test_incomplete_keeps_old(self):
        (self.root/'rmlarm.apk').write_bytes(b'old'); s=self.store.create('admin',self.data)
        with self.assertRaises(Fault): self.store.finish(self.store.metadata(s['id'],'admin'))
        self.assertEqual((self.root/'rmlarm.apk').read_bytes(),b'old')
    def test_metadata_verified(self):
        m=self.upload()
        for info in [('evil.package',4,'1.0.3'),('com.example.rmdckentreprenadab',3,'1.0.3'),('com.example.rmdckentreprenadab',4,'wrong')]:
            self.store.inspector=lambda p,result=info:result
            with self.assertRaises(Fault): self.store.finish(m)
            self.assertFalse((self.root/'app-update.json').exists())
    def test_checksum_verified(self):
        m=self.upload(); m['sha256']='a'*64
        with self.assertRaises(Fault): self.store.finish(m)
    def test_atomic_release(self):
        (self.root/'rmlarm.apk').write_bytes(b'old')
        (self.root/'app-update.json').write_text(json.dumps(dict(versionCode=3,versionName='1.0.2',apkUrl=APK_BASE+'rmlarm.apk')))
        self.assertEqual(self.store.finish(self.upload())['state'],'complete')
        self.assertEqual((self.root/'rmlarm.apk').read_bytes(),self.bytes)
        release=json.loads((self.root/'app-update.json').read_text())
        self.assertEqual(release['versionCode'],4)
        self.assertEqual(release['apkUrl'],APK_BASE+'rmlarm.apk')
        self.assertEqual(release['versionName'],'1.0.3')
    def test_fixed_paths(self):
        with self.assertRaises(Fault): self.store.metadata('../../etc/passwd','admin')
        with self.assertRaises(Fault): self.store.create('admin',dict(self.data,path='/etc/passwd'))
    def test_partial_files_are_outside_public_www(self):
        self.upload()
        self.assertFalse(self.store.parts.is_relative_to(self.root.parent))
        self.assertEqual(self.store.parts,Path(self.tmp.name)/'.rm-apk-uploads')
    def test_retry_completed_release_does_not_upload_again(self):
        m=self.upload(); self.store.finish(m)
        result=self.store.create('admin',self.data)
        self.assertEqual(result['id'],m['id'])
        self.assertEqual(result['state'],'complete')
    def test_changed_current_notes_are_not_treated_as_same_release(self):
        m=self.upload(); self.store.finish(m)
        manifest=self.root/'app-update.json'
        current=json.loads(manifest.read_text()); current['message']='Senare publicering'
        manifest.write_text(json.dumps(current))
        result=self.store.create('admin',self.data)
        self.assertNotEqual(result['id'],m['id'])
        self.assertEqual(result['state'],'uploading')
        self.assertEqual(json.loads(manifest.read_text())['message'],'Senare publicering')
