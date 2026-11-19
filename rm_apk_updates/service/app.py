import asyncio
import json
from pathlib import Path
from aiohttp import web, ClientSession, ClientTimeout
from .store import Store, Fault, CHUNK

AUTH = 'https://rm-larm-styrning.lovable.app/api/public/apk-admin'
ORIGINS = {'https://rm-larm-styrning.lovable.app', 'https://www.msdev.bid', 'https://msdev.bid', 'https://id-preview--cbf4c4d7-d235-4a5d-838d-6b5ca8d69616.lovable.app'}

# HA Supervisor's ingress proxy; with ingress the browser never reaches this container directly.
INGRESS_PEER = '172.30.32.2'

async def bounded_json(request):
    body = bytearray()
    async for part in request.content.iter_chunked(8192):
        body.extend(part)
        if len(body) > 8192: raise Fault('Ogiltiga metadata.')
    try: return json.loads(body)
    except ValueError: raise Fault('Ogiltiga metadata.') from None

def app_for(store, authorize, peers=None):
    lock = asyncio.Lock()
    @web.middleware
    async def gate(request, handler):
        origin = request.headers.get('Origin')
        if origin and origin not in ORIGINS: return web.json_response({'error': 'Webbadressen är inte tillåten.'}, status=403)
        headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
        if origin:
            headers.update({'Access-Control-Allow-Origin': origin, 'Vary': 'Origin', 'Access-Control-Allow-Methods': 'GET, POST, PATCH, OPTIONS', 'Access-Control-Allow-Headers': 'Authorization, Content-Type, Upload-Offset', 'Access-Control-Max-Age': '600'})
        if request.method == 'OPTIONS': return web.Response(status=204, headers=headers)
        try:
            if peers is not None and request.remote not in peers: raise Fault('Endast Home Assistant-ingress är tillåten.', 403)
            if request.path != '/healthz':
                # Ingress may not forward Authorization; the RM relay also sends the opaque permission here.
                permission = request.headers.get('X-RM-Permission', '')
                bearer = 'Bearer ' + permission if permission else request.headers.get('Authorization', '')
                if not bearer.startswith('Bearer rmapk.') or len(bearer) > 16400: raise Fault('Kortlivad uppladdningsbehörighet krävs.', 401)
                request['user'] = await authorize(bearer)
            response = await handler(request)
        except Fault as error: response = web.json_response({'error': str(error)}, status=error.status)
        except Exception: response = web.json_response({'error': 'Uppladdningen kunde inte slutföras. Kontrollera konfiguration och diskutrymme.'}, status=503)
        response.headers.update(headers)
        return response
    app = web.Application(middlewares=[gate], client_max_size=CHUNK)
    async def health(request): return web.json_response({'ok': True})
    async def space(request):
        async with lock: return web.json_response({'availableBytes': await asyncio.to_thread(store.space), 'packageName': store.package})
    async def create(request):
        data = await bounded_json(request)
        async with lock: return web.json_response(await asyncio.to_thread(store.create, request['user'], data))
    async def create_file(request):
        data = await bounded_json(request)
        async with lock: return web.json_response(await asyncio.to_thread(store.create_file, request['user'], data))
    async def finish_file(request):
        async with lock:
            m = store.metadata(request.match_info['id'], request['user'])
            if m.get('kind') != 'file': raise Fault('Fel uppladdningstyp.', 409)
            return web.json_response(await asyncio.to_thread(store.finish_file, m))
    async def status(request):
        async with lock: return web.json_response(store.status(store.metadata(request.match_info['id'], request['user'])))
    async def chunk(request):
        # Stream incoming data in small pieces; never allocate the entire APK.
        data = bytearray()
        async for part in request.content.iter_chunked(65536):
            data.extend(part)
            if len(data) > CHUNK: raise Fault('Fildelen är för stor.', 413)
        try: offset = int(request.headers.get('Upload-Offset', '-1'))
        except ValueError: raise Fault('Ogiltig byteposition.') from None
        async with lock:
            m = store.metadata(request.match_info['id'], request['user'])
            return web.json_response(await asyncio.to_thread(store.append, m, offset, data))
    async def finish(request):
        async with lock:
            m = store.metadata(request.match_info['id'], request['user'])
            return web.json_response(await asyncio.to_thread(store.finish, m))
    app.add_routes([web.get('/healthz', health), web.get('/v1/apk/space', space), web.post('/v1/apk/uploads', create), web.get('/v1/apk/uploads/{id}', status), web.patch('/v1/apk/uploads/{id}', chunk), web.post('/v1/apk/uploads/{id}/finish', finish),
        web.get('/v1/files/space', space), web.post('/v1/files/uploads', create_file), web.get('/v1/files/uploads/{id}', status), web.patch('/v1/files/uploads/{id}', chunk), web.post('/v1/files/uploads/{id}/finish', finish_file)])
    return app

async def main():
    config = json.loads(Path('/data/options.json').read_text())
    package = config['package_name']
    import re
    if not re.fullmatch(r'[A-Za-z][\w.]{0,199}', package): raise ValueError('configuration')
    # homeassistant_config is mounted at /homeassistant by HA Supervisor.
    store = Store('/homeassistant/www/apk', package)
    async with ClientSession(timeout=ClientTimeout(total=10), trust_env=False) as http:
        async def authorize(bearer):
            async with http.post(AUTH, headers={'Authorization': bearer}, allow_redirects=False) as response:
                if response.status in (401, 403): raise Fault('Godkänd administratör krävs.', response.status)
                if response.status != 200: raise Fault('RM-behörigheten kunde inte verifieras.', 503)
                value = await response.json()
                return value['userId']
        runner = web.AppRunner(app_for(store, authorize, {INGRESS_PEER}), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, '0.0.0.0', 8100).start()
        print('RM APK listening on internal port 8100 for Home Assistant ingress only.', flush=True)
        await asyncio.Event().wait()
if __name__ == '__main__':
    try: asyncio.run(main())
    except Exception:
        print('RM APK stopped: check local configuration, fixed directory or internal port.', flush=True)
        raise SystemExit(1) from None
