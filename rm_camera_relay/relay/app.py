import asyncio
import json
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from aiohttp import ClientSession, ClientTimeout, WSMsgType, web
from .security import ORIGIN, INTROSPECT, Replay, digest, grant, private_key, proof, public_key, sign
from .media import source_path, rewrite

# Neither aiohttp access logging nor exception details may disclose bearer/proof/source resources.
logging.disable(logging.CRITICAL)
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}


def local_site(runner):
    # Fixed HA-host loopback only: Serve owns external TLS/8443. Never use forwarded
    # headers to establish identity or change the proof's fixed external HTTPS origin.
    return web.TCPSite(runner, "127.0.0.1", 8099)


class Denied(Exception):
    def __init__(self, status=403):
        self.status = status


@dataclass
class Stream:
    subject: str
    entity: str
    jkt: str
    expires: int
    grant_id: str
    resources: dict = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Relay:
    def __init__(self, rm_key, service_key, ha_token, http=None):
        self.rm_key = rm_key
        self.service_key = service_key
        self.ha_token = ha_token
        self.http = http
        self.sessions = {}
        self.replay = Replay()
        self.opens = Replay()
        self.busy = 0

    async def introspect(self, token, g):
        now = int(time.time())
        body = json.dumps({"grant": token}, separators=(",", ":")).encode()
        assertion = sign({"iss": "rm-camera-relay", "aud": INTROSPECT, "htm": "POST", "bh": digest(body), "iat": now, "exp": now + 30, "nonce": str(uuid.uuid4())}, self.service_key)
        try:
            async with self.http.post(INTROSPECT, data=body, headers={"Content-Type": "application/json", "X-RM-Relay-Assertion": assertion}, allow_redirects=False, timeout=ClientTimeout(total=3)) as response:
                if response.status != 200:
                    raise Denied()
                data = json.loads(await self.read(response, 1024))
                if data != {"active": True, "entityId": g["entity"], "expiresAt": g["exp"]}:
                    raise Denied()
        except Denied:
            raise
        except Exception:
            raise Denied(503) from None

    async def read(self, response, limit):
        result = bytearray()
        async for chunk in response.content.iter_chunked(65536):
            result.extend(chunk)
            if len(result) > limit:
                raise Denied(502)
        return bytes(result)

    async def hls(self, entity):
        try:
            async with asyncio.timeout(3):
                async with self.http.ws_connect("http://supervisor/core/websocket", max_msg_size=8192, timeout=ClientTimeout(total=3)) as ws:
                    async for msg in ws:
                        if msg.type != WSMsgType.TEXT:
                            raise Denied(502)
                        data = json.loads(msg.data)
                        if data.get("type") == "auth_required":
                            await ws.send_json({"type": "auth", "access_token": self.ha_token})
                        elif data.get("type") == "auth_ok":
                            await ws.send_json({"id": 1, "type": "camera/stream", "entity_id": entity, "format": "hls"})
                        elif data.get("type") == "auth_invalid":
                            raise Denied(502)
                        elif data.get("type") == "result" and data.get("id") == 1:
                            if data.get("success") is not True:
                                raise Denied(502)
                            return source_path(data["result"]["url"])
            raise Denied(502)
        except Denied:
            raise
        except TimeoutError:
            raise Denied(504) from None
        except Exception:
            raise Denied(502) from None

    def prune(self):
        now = time.time()
        self.sessions = {k: s for k, s in self.sessions.items() if s.expires > now}

    def authorize(self, request, body):
        if request.query_string or '%' in request.raw_path or request.headers.get('Range'):
            raise Denied(400)
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer ") or len(header) > 4103:
            raise Denied(401)
        token = header[7:]
        try:
            g = grant(token, self.rm_key)
            p = proof(request.headers.get("X-RM-Proof", ""), g, request.method, ORIGIN + request.path, body, self.replay)
            if p["ath"] != digest(token.encode()):
                raise ValueError("token binding")
        except Exception:
            raise Denied() from None
        return token, g

    def resource(self, stream_id, s, path):
        for opaque, value in s.resources.items():
            if value == path:
                return f"/v1/streams/{stream_id}/resources/{opaque}"
        if len(s.resources) >= 1024:
            raise Denied(503)
        opaque = secrets.token_urlsafe(24)
        s.resources[opaque] = path
        return f"/v1/streams/{stream_id}/resources/{opaque}"

    async def open(self, request):
        body = await request.read()
        token, g = self.authorize(request, body)
        if body != b"{}":
            raise Denied(400)
        self.prune()
        if len(self.sessions) + self.busy >= 32 or sum(s.subject == g["sub"] for s in self.sessions.values()) >= 4:
            raise Denied(429)
        try:
            self.opens.consume(g["jti"], time.time(), 60)
        except ValueError:
            raise Denied() from None
        self.busy += 1
        try:
            await self.introspect(token, g)
            path = await self.hls(g["entity"])
            if time.time() >= g["exp"]:
                raise Denied(410)
            if sum(s.subject == g["sub"] for s in self.sessions.values()) >= 4:
                raise Denied(429)
            stream_id = secrets.token_urlsafe(24)
            s = Stream(g["sub"], g["entity"], g["cnf"]["jkt"], g["exp"], g["jti"])
            manifest = self.resource(stream_id, s, path)
            self.sessions[stream_id] = s
            return web.json_response({"streamId": stream_id, "manifestPath": manifest, "expiresAt": s.expires})
        finally:
            self.busy -= 1

    async def stream(self, request):
        body = await request.read()
        token, g = self.authorize(request, body)
        self.prune()
        stream_id = request.match_info["id"]
        s = self.sessions.get(stream_id)
        if s is None:
            raise Denied(410)
        async with s.lock:
            if self.sessions.get(stream_id) is not s or time.time() >= s.expires:
                raise Denied(410)
            if (s.subject, s.entity, s.jkt) != (g["sub"], g["entity"], g["cnf"]["jkt"]):
                raise Denied()
            if request.method == "POST":
                if body != b"{}" or g["jti"] == s.grant_id or g["exp"] <= s.expires:
                    raise Denied(400)
                try:
                    self.opens.consume(g["jti"], time.time(), 60)
                    await self.introspect(token, g)
                    if time.time() >= min(g["exp"], s.expires):
                        raise Denied(410)
                except Exception:
                    self.sessions.pop(stream_id, None)
                    raise Denied() from None
                s.expires, s.grant_id = g["exp"], g["jti"]
                return web.json_response({"expiresAt": s.expires})
            if g["jti"] != s.grant_id:
                raise Denied()
            if request.method == "DELETE":
                self.sessions.pop(stream_id, None)
                return web.Response(status=204)
            if body:
                raise Denied(400)
            path = s.resources.get(request.match_info["resource"])
            if path is None:
                raise Denied(404)
            try:
                timeout = min(3, s.expires - time.time())
                async with self.http.get("http://supervisor/core" + path, headers={"Authorization": "Bearer " + self.ha_token}, allow_redirects=False, timeout=ClientTimeout(total=timeout)) as response:
                    if response.status != 200:
                        raise Denied(502)
                    data = await self.read(response, 16 * 1024 * 1024)
                if time.time() >= min(g["exp"], s.expires):
                    self.sessions.pop(stream_id, None)
                    raise Denied(410)
                if data.startswith(b"#EXTM3U"):
                    data = rewrite(data.decode("utf-8", errors="strict"), path, lambda p: self.resource(stream_id, s, p)).encode()
                    mime = "application/vnd.apple.mpegurl"
                elif path.endswith(".ts"):
                    mime = "video/mp2t"
                elif path.endswith((".m4s", ".mp4")):
                    mime = "video/mp4"
                elif path.endswith(".key") and len(data) == 16:
                    mime = "application/octet-stream"
                else:
                    raise Denied(502)
                return web.Response(body=data, content_type=mime)
            except Denied:
                raise
            except TimeoutError:
                raise Denied(504) from None
            except Exception:
                raise Denied(502) from None


def app_for(relay):
    @web.middleware
    async def guard(request, handler):
        try:
            result = await handler(request)
        except Denied as error:
            result = web.json_response({"error": "relay_request_denied", "status": error.status}, status=error.status)
        except web.HTTPException as error:
            result = web.json_response({"error": "relay_request_denied", "status": error.status}, status=error.status)
        except Exception:
            result = web.json_response({"error": "relay_unavailable", "status": 503}, status=503)
        result.headers.update(NO_STORE)
        return result

    app = web.Application(client_max_size=8192, middlewares=[guard])
    async def health(_):
        return web.json_response({"ok": True})
    app.router.add_get("/healthz", health)
    app.router.add_post("/v1/streams/open", relay.open)
    app.router.add_post("/v1/streams/{id}/renew", relay.stream)
    app.router.add_delete("/v1/streams/{id}", relay.stream)
    app.router.add_get("/v1/streams/{id}/resources/{resource}", relay.stream, allow_head=False)
    return app


async def start():
    # Fixed paths: options cannot read arbitrary host files; /ssl is read-only inside this container.
    config = json.loads(Path("/data/options.json").read_text())
    def ssl_file(name):
        path = Path(config[name])
        if path.parent != Path("/ssl") or path.name.startswith("."):
            raise ValueError("configuration")
        return str(path)
    rm_key = public_key(json.loads(config["rm_grant_public_jwk"]))
    service_key = private_key(json.loads(Path(ssl_file("relay_private_jwk_file")).read_text()))
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise ValueError("configuration")
    async with ClientSession(timeout=ClientTimeout(total=3), trust_env=False) as http:
        relay = Relay(rm_key, service_key, token, http)
        runner = web.AppRunner(app_for(relay), access_log=None)
        await runner.setup()
        await local_site(runner).start()
        try:
            while True:
                await asyncio.sleep(1)
                relay.prune()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        pass
    except Exception:
        # Do not print filesystem/key/HTTP exceptions or dump configuration.
        print("RM Camera Relay stopped: check local configuration/loopback port.", flush=True)
        raise SystemExit(1) from None
