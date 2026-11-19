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
        self.client=TestClient(TestServer(app_for(Store(Path(self.tmp.name)/'www'/'apk','com.example.rm'),auth))); await self.client.start_server()
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
        store=Store(Path(self.tmp.name)/'www'/'apk','com.example.rm',lambda p:('com.example.rm',4,'1.0.3'))
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
