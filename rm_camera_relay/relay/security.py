import base64
import hashlib
import json
import time
import uuid
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature

ORIGIN = "https://homeassistant.tailc16667.ts.net:8443"
ISSUER = "https://www.msdev.bid/"
INTROSPECT = ISSUER + "api/public/mobile-camera-grant/introspect"
CAMERAS = frozenset(("camera.ipc_substream", "camera.ipc_substream_2"))


def encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def digest(value):
    return encode(hashlib.sha256(value).digest())


def public_key(jwk):
    if set(jwk) != {"kty", "crv", "x", "y"} or jwk["kty"] != "EC" or jwk["crv"] != "P-256":
        raise ValueError("key")
    if len(decode(jwk["x"])) != 32 or len(decode(jwk["y"])) != 32:
        raise ValueError("key")
    return ec.EllipticCurvePublicNumbers(int.from_bytes(decode(jwk["x"]), "big"), int.from_bytes(decode(jwk["y"]), "big"), ec.SECP256R1()).public_key()


def private_key(jwk):
    public = {k: jwk[k] for k in ("kty", "crv", "x", "y")}
    key = ec.derive_private_key(int.from_bytes(decode(jwk["d"]), "big"), ec.SECP256R1())
    if key.public_key().public_numbers() != public_key(public).public_numbers():
        raise ValueError("key")
    return key


def thumbprint(jwk):
    public_key(jwk)
    return digest(json.dumps(jwk, sort_keys=True, separators=(",", ":")).encode())


def sign(payload, key):
    header = encode(b'{"alg":"ES256","typ":"JWT"}')
    body = encode(json.dumps(payload, separators=(",", ":")).encode())
    value = (header + "." + body).encode()
    r, s = decode_dss_signature(key.sign(value, ec.ECDSA(hashes.SHA256())))
    return value.decode() + "." + encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def verify(token, key):
    if len(token) > 4096:
        raise ValueError("token")
    header, body, signature = token.split(".")
    if json.loads(decode(header)) != {"alg": "ES256", "typ": "JWT"}:
        raise ValueError("algorithm")
    raw = decode(signature)
    if len(raw) != 64:
        raise ValueError("signature")
    key.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")), (header + "." + body).encode(), ec.ECDSA(hashes.SHA256()))
    return json.loads(decode(body))


def grant(token, key, now=None):
    now = int(time.time()) if now is None else now
    g = verify(token, key)
    if set(g) != {"iss", "aud", "sub", "entity", "scope", "iat", "nbf", "exp", "jti", "cnf"}:
        raise ValueError("grant")
    uuid.UUID(g["sub"]); uuid.UUID(g["jti"])
    if g["iss"] != ISSUER or g["aud"] != "rm-ha-camera-relay" or g["scope"] != "camera:stream" or g["entity"] not in CAMERAS:
        raise ValueError("scope")
    if any(type(g[k]) is not int for k in ("iat", "nbf", "exp")) or g["nbf"] != g["iat"] or g["exp"] - g["iat"] != 60 or not g["iat"] <= now < g["exp"]:
        raise ValueError("expired")
    if set(g["cnf"]) != {"jkt"} or not isinstance(g["cnf"]["jkt"], str) or len(g["cnf"]["jkt"]) != 43:
        raise ValueError("binding")
    return g


class Replay:
    def __init__(self):
        self.entries = {}

    def consume(self, nonce, now, lifetime=90):
        uuid.UUID(nonce)
        self.entries = {n: expiry for n, expiry in self.entries.items() if expiry > now}
        if nonce in self.entries or len(self.entries) >= 10000:
            raise ValueError("replay")
        self.entries[nonce] = now + lifetime


def proof(token, g, method, target, body, replay, now=None):
    now = int(time.time()) if now is None else now
    # Read only the bounded public JWK before verifying; no header-supplied algorithm or URL.
    p = json.loads(decode(token.split(".")[1]))
    if set(p) != {"jwk", "htm", "htu", "bh", "ath", "iat", "nonce"}:
        raise ValueError("proof")
    p = verify(token, public_key(p["jwk"]))
    if thumbprint(p["jwk"]) != g["cnf"]["jkt"] or p["htm"] != method or p["htu"] != target or p["bh"] != digest(body):
        raise ValueError("binding")
    if type(p["iat"]) is not int or not now - 30 <= p["iat"] <= now:
        raise ValueError("time")
    replay.consume(p["nonce"], now)
    return p