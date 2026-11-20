import tempfile, unittest
from pathlib import Path
from aiohttp.test_utils import TestClient, TestServer
from service.app import app_for
from service.store import Store, Fault
class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        async def auth(bearer):
            if bearer!='Bearer rmapk.approved-admin': raise Fault('Godkänd administratör krävs.',403)
            return 'admin'
        self.client=TestClient(TestServer(app_for(Store(Path(self.tmp.name)/'www'/'apk','com.example.rmdckentreprenadab'),auth))); await self.client.start_server()
    async def asyncTearDown(self): await self.client.close(); self.tmp.cleanup()
    async def test_missing_login(self): self.assertEqual((await self.client.get('/v1/apk/space')).status,401)
    async def test_nonadmin(self): self.assertEqual((await self.client.get('/v1/apk/space',headers={'Authorization':'Bearer rmapk.member'})).status,403)
    async def test_rm_session_is_not_upload_permission(self): self.assertEqual((await self.client.get('/v1/apk/space',headers={'Authorization':'Bearer approved-admin'})).status,401)
    async def test_admin(self):
        r=await self.client.get('/v1/apk/space',headers={'Authorization':'Bearer rmapk.approved-admin','Origin':'https://rm-larm-styrning.lovable.app'})
        self.assertEqual(r.status,200); self.assertGreater((await r.json())['availableBytes'],0); self.assertEqual(r.headers['Cache-Control'],'no-store')
    async def test_origin(self): self.assertEqual((await self.client.get('/v1/apk/space',headers={'Authorization':'Bearer rmapk.approved-admin','Origin':'https://evil.example'})).status,403)
    async def test_full_upload_reads_back_after_interruption(self):
        import hashlib
        body=b'verified-fixture-apk'
        store=Store(Path(self.tmp.name)/'www'/'apk','com.example.rmdckentreprenadab',lambda p:('com.example.rmdckentreprenadab',4,'1.0.3'))
        async def auth(bearer): return 'admin'
        client=TestClient(TestServer(app_for(store,auth)))
        await client.start_server()
        headers={'Authorization':'Bearer rmapk.test-admin'}
        try:
            r=await client.post('/v1/apk/uploads',headers=headers,json=dict(size=len(body),sha256=hashlib.sha256(body).hexdigest(),versionCode=4,versionName='1.0.3',message='Update'))
            self.assertEqual(r.status,200); ident=(await r.json())['id']
            r=await client.patch('/v1/apk/uploads/'+ident,headers=dict(headers,**{'Upload-Offset':'0'}),data=body[:3])
            self.assertEqual((await r.json())['offset'],3)
            r=await client.get('/v1/apk/uploads/'+ident,headers=headers)
            self.assertEqual((await r.json())['offset'],3)
            r=await client.patch('/v1/apk/uploads/'+ident,headers=dict(headers,**{'Upload-Offset':'3'}),data=body[3:])
            self.assertEqual((await r.json())['offset'],len(body))
            r=await client.post('/v1/apk/uploads/'+ident+'/finish',headers=headers)
            self.assertEqual(r.status,200); self.assertEqual((await r.json())['state'],'complete')
            self.assertEqual((store.root/'rmlarm.apk').read_bytes(),body)
        finally: await client.close()
    async def test_small_file_and_apk_sized_transfer_replace_existing_file(self):
        """Real HTTP bytes/local disk; synthetic inspector is not APK signature proof."""
        import hashlib
        from service.store import CHUNK
        root=Path(self.tmp.name)/'size-probe'/'www'/'apk'
        store=Store(root,'com.example.rmdckentreprenadab',lambda p:('com.example.rmdckentreprenadab',4,'1.0.3'))
        root.joinpath('rmlarm.apk').write_bytes(b'old-release')
        async def auth(bearer):
            if bearer != 'Bearer rmapk.approved-admin': raise Fault('Godkänd administratör krävs.',403)
            return 'admin'
        client=TestClient(TestServer(app_for(store,auth)))
        await client.start_server()
        headers={'Authorization':'Bearer rmapk.approved-admin'}
        try:
            for size in (1024,40*1024*1024):
                with self.subTest(size=size):
                    block=b'0123456789abcdef'*4096
                    digest=hashlib.sha256()
                    for offset in range(0,size,len(block)): digest.update(block[:min(len(block),size-offset)])
                    old=root.joinpath('rmlarm.apk').read_bytes()
                    r=await client.post('/v1/apk/uploads',headers=headers,json=dict(size=size,sha256=digest.hexdigest(),versionCode=4,versionName='1.0.3',message='Size probe'))
                    self.assertEqual(r.status,200); ident=(await r.json())['id']
                    offset=0
                    while offset<size:
                        length=min(CHUNK,size-offset)
                        chunk=(block*((length+len(block)-1)//len(block)))[:length]
                        r=await client.patch('/v1/apk/uploads/'+ident,headers=dict(headers,**{'Upload-Offset':str(offset)}),data=chunk)
                        self.assertEqual(r.status,200)
                        offset+=length
                        self.assertEqual((await r.json())['offset'],offset)
                    self.assertEqual(root.joinpath('rmlarm.apk').read_bytes(),old)
                    r=await client.post('/v1/apk/uploads/'+ident+'/finish',headers=headers)
                    self.assertEqual(r.status,200)
                    self.assertEqual(root.joinpath('rmlarm.apk').stat().st_size,size)
                    with root.joinpath('rmlarm.apk').open('rb') as file:
                        self.assertEqual(hashlib.file_digest(file,'sha256').hexdigest(),digest.hexdigest())
                    self.assertEqual(root.joinpath('app-update.json').exists(),True)
        finally: await client.close()

class FileUploadTests(unittest.IsolatedAsyncioTestCase):
    """Generic www/apk uploads: real HTTP bytes and disk; replacement must be atomic and hash-verified."""
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'www'/'apk','com.example.rmdckentreprenadab',lambda p:(_ for _ in ()).throw(AssertionError('no apk inspection')))
        async def auth(bearer):
            if bearer!='Bearer rmapk.approved-admin': raise Fault('Godkänd administratör krävs.',403)
            return 'admin'
        self.client=TestClient(TestServer(app_for(self.store,auth))); await self.client.start_server()
        self.h={'X-RM-Permission':'rmapk.approved-admin'}
    async def asyncTearDown(self): await self.client.close(); self.tmp.cleanup()
    async def upload(self,name,body):
        import hashlib
        r=await self.client.post('/v1/files/uploads',headers=self.h,json=dict(name=name,size=len(body),sha256=hashlib.sha256(body).hexdigest()))
        self.assertEqual(r.status,200,await r.text()); s=await r.json()
        while s['offset']<len(body):
            r=await self.client.patch('/v1/files/uploads/'+s['id'],headers=dict(self.h,**{'Upload-Offset':str(s['offset'])}),data=body[s['offset']:s['offset']+s['chunkBytes']])
            self.assertEqual(r.status,200,await r.text()); s=await r.json()
        return await self.client.post('/v1/files/uploads/'+s['id']+'/finish',headers=self.h)
    async def test_small_and_apk_sized_files_replace_same_name(self):
        target=self.store.root/'test.bin'; target.write_bytes(b'old')
        for size in (1024, 40*1024*1024):
            body=(b'0123456789abcdef'*(size//16+1))[:size]
            r=await self.upload('test.bin',body)
            self.assertEqual(r.status,200); self.assertEqual((await r.json())['state'],'complete')
            self.assertEqual(target.read_bytes(),body)
        self.assertEqual([p.name for p in self.store.root.iterdir()],['test.bin'])
        self.assertEqual(list(self.store.parts.glob('*.part')),[])
    async def test_hash_mismatch_keeps_existing_file(self):
        import hashlib
        target=self.store.root/'test.txt'; target.write_bytes(b'old')
        r=await self.client.post('/v1/files/uploads',headers=self.h,json=dict(name='test.txt',size=3,sha256=hashlib.sha256(b'xyz').hexdigest()))
        s=await r.json()
        await self.client.patch('/v1/files/uploads/'+s['id'],headers=dict(self.h,**{'Upload-Offset':'0'}),data=b'abc')
        r=await self.client.post('/v1/files/uploads/'+s['id']+'/finish',headers=self.h)
        self.assertEqual(r.status,400); self.assertIn('Kontrollsumman',(await r.json())['error'])
        self.assertEqual(target.read_bytes(),b'old')
    async def test_rejects_paths_hidden_and_manifest_names(self):
        for name in ('../x','a/b','.hidden','app-update.json','x.part','','rmlarm-'+'a'*64+'.apk'):
            r=await self.client.post('/v1/files/uploads',headers=self.h,json=dict(name=name,size=1,sha256='0'*64))
            self.assertEqual(r.status,400,name)
    async def test_requires_admin_permission(self):
        self.assertEqual((await self.client.post('/v1/files/uploads',json={})).status,401)
        self.assertEqual((await self.client.post('/v1/files/uploads',headers={'X-RM-Permission':'rmapk.member'},json={})).status,403)
    async def test_only_ingress_peer(self):
        async def auth(bearer): return 'admin'
        client=TestClient(TestServer(app_for(self.store,auth,{'172.30.32.2'}))); await client.start_server()
        try: self.assertEqual((await client.get('/v1/files/space',headers=self.h)).status,403)
        finally: await client.close()
