"""Run locally on a trusted computer. Writes keys to files, never stdout."""
import json
import os
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import ec
from relay.security import encode

def generate(directory, name):
    key = ec.generate_private_key(ec.SECP256R1())
    numbers = key.private_numbers()
    public = {"kty": "EC", "crv": "P-256", "x": encode(numbers.public_numbers.x.to_bytes(32, "big")), "y": encode(numbers.public_numbers.y.to_bytes(32, "big"))}
    for suffix, value in (("public", public), ("private", {**public, "d": encode(numbers.private_value.to_bytes(32, "big"))})):
        path = directory / f"{name}-{suffix}.jwk"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(value, file, separators=(",", ":"))

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    args.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    generate(args.directory, "rm-camera-grant")
    generate(args.directory, "rm-camera-relay")
    print("Created two P-256 key pairs in private files; no key values printed.")