"""Software ES256 authenticator for real cryptographic verification; not device evidence."""

import hashlib
import json
import secrets
from uuid import UUID

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import bytes_to_base64url


class SoftwarePasskey:
    def __init__(self, rp_id="auth.example.test", origin="https://auth.example.test"):
        self.rp_id, self.origin = rp_id, origin
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(32)
        self.counter = 0

    def client_data(self, ceremony, challenge):
        return json.dumps(
            {"type": ceremony, "challenge": challenge, "origin": self.origin, "crossOrigin": False},
            separators=(",", ":"),
        ).encode()

    def registration(self, options, *, verified=True):
        public = self.key.public_key().public_numbers()
        cose = cbor2.dumps(
            {1: 2, 3: -7, -1: 1, -2: public.x.to_bytes(32, "big"), -3: public.y.to_bytes(32, "big")}
        )
        auth = (
            hashlib.sha256(self.rp_id.encode()).digest()
            + bytes([0x45 if verified else 0x41])
            + bytes(4)
            + bytes(16)
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + cose
        )
        attestation = cbor2.dumps({"fmt": "none", "authData": auth, "attStmt": {}})
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "attestationObject": bytes_to_base64url(attestation),
                "clientDataJSON": bytes_to_base64url(
                    self.client_data("webauthn.create", options["challenge"])
                ),
            },
        }

    def assertion(self, options, owner, *, verified=True):
        self.counter += 1
        client = self.client_data("webauthn.get", options["challenge"])
        auth = (
            hashlib.sha256(self.rp_id.encode()).digest()
            + bytes([5 if verified else 1])
            + self.counter.to_bytes(4, "big")
        )
        signature = self.key.sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "authenticatorData": bytes_to_base64url(auth),
                "clientDataJSON": bytes_to_base64url(client),
                "signature": bytes_to_base64url(signature),
                "userHandle": bytes_to_base64url(UUID(owner).bytes),
            },
        }
