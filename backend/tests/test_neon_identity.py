"""Faculty Google sign-in through Neon Auth: JWT verification and invite linking."""
import base64
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory(prefix="learningx-neon-identity-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from learningx import neon_identity as ni
from learningx.api import app
from learningx.security import decode_token
from learningx.storage import SessionLocal, User, uid

AUTH_URL = "https://ep-test.neonauth.example.neon.tech/learningx/auth"
ORIGIN = "https://ep-test.neonauth.example.neon.tech"


def keypair(kid):
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return private, {"kty": "OKP", "crv": "Ed25519", "kid": kid, "x": base64.urlsafe_b64encode(raw).rstrip(b"=").decode()}


class Creds:
    def __init__(self, token):
        self.credentials = token


class NeonIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        self.private, jwk = keypair("k1")
        self.jwks = {"keys": [jwk]}
        self.fetches = 0

        def fetch(url):
            self.fetches += 1
            self.assertEqual(url, AUTH_URL + "/.well-known/jwks.json")
            return {k["kid"]: ni.Ed25519PublicKey.from_public_bytes(
                base64.urlsafe_b64decode(k["x"] + "==")) for k in self.jwks["keys"]}

        ni._jwks.update(keys={}, fetched=0.0, url=None)
        with SessionLocal() as db:
            faculty = User(email=f"invited-{uid()}@test.local", role="faculty")
            other = User(email=f"invited2-{uid()}@test.local", role="faculty")
            db.add_all([faculty, other])
            db.commit()
            self.faculty_id, self.other_id = faculty.id, other.id
        self.invite = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
        self.invite2 = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
        invites = {hashlib.sha256(self.invite.encode()).hexdigest(): self.faculty_id,
                   hashlib.sha256(self.invite2.encode()).hexdigest(): self.other_id}
        self.patches = [patch.dict(os.environ, {"NIT_NEON_AUTH_URL": AUTH_URL,
                                                "NIT_BETA_FACULTY_INVITES": json.dumps(invites)}),
                        patch.object(ni, "_fetch_jwks", fetch)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def token(self, sub=None, key=None, kid="k1", **claims):
        now = int(time.time())
        payload = {"sub": sub or ("neon-" + uid()), "email": "teacher@gmail.com", "iat": now, "exp": now + 900,
                   "iss": ORIGIN, "aud": ORIGIN, "role": "authenticated", "banned": False, **claims}
        return jwt.encode(payload, key or self.private, algorithm="EdDSA", headers={"kid": kid})

    def exchange(self, token, invite=None):
        body = {"token": token, **({"invite": invite} if invite else {})}
        return self.client.post("/v1/auth/neon/session", json=body)

    def test_first_sign_in_links_with_an_invite_then_signs_in_directly(self):
        sub = "neon-" + uid()
        first = self.exchange(self.token(sub))
        self.assertEqual((first.status_code, first.json()["detail"]["code"]), (403, "invite_required"))
        linked = self.exchange(self.token(sub), self.invite)
        self.assertEqual(linked.status_code, 200, linked.text)
        self.assertEqual(linked.headers["cache-control"], "no-store")
        self.assertEqual(decode_token(Creds(linked.json()["access_token"]))["sub"], self.faculty_id)
        again = self.exchange(self.token(sub))
        self.assertEqual(decode_token(Creds(again.json()["access_token"]))["sub"], self.faculty_id,
                         "later sign-ins need no invite")
        courses = self.client.get("/v1/courses/mine", headers={"Authorization": "Bearer " + again.json()["access_token"]})
        self.assertEqual(courses.status_code, 200, "the exchanged token works on existing APIs")

    def test_an_invite_links_only_one_google_account(self):
        self.assertEqual(self.exchange(self.token(), self.invite).status_code, 200)
        taken = self.exchange(self.token(), self.invite)
        self.assertEqual((taken.status_code, taken.json()["detail"]["code"]), (409, "invite_already_linked"))
        self.assertEqual(self.exchange(self.token(), "A" * 43).status_code, 401)

    def test_linking_is_by_neon_user_id_not_email(self):
        sub = "neon-" + uid()
        self.exchange(self.token(sub, email="shared@gmail.com"), self.invite)
        imposter = self.exchange(self.token(email="shared@gmail.com"))
        self.assertEqual(imposter.json()["detail"]["code"], "invite_required", "same email, different account")

    def test_tokens_that_fail_verification_are_rejected(self):
        other_key, _ = keypair("k1")
        now = int(time.time())
        cases = {
            "wrong signature": self.token(key=other_key),
            "expired": self.token(iat=now - 2000, exp=now - 1000),
            "wrong issuer": self.token(iss="https://evil.example.com"),
            "wrong audience": self.token(aud="https://evil.example.com"),
            "banned": self.token(banned=True),
            "unknown kid": self.token(kid="nope"),
            "not eddsa": jwt.encode({"sub": "x", "iss": ORIGIN, "aud": ORIGIN, "iat": now, "exp": now + 60},
                                    "a" * 40, algorithm="HS256", headers={"kid": "k1"}),
            "garbage": "x" * 40,
        }
        for name, token in cases.items():
            with self.subTest(name):
                self.assertEqual(self.exchange(token, self.invite).status_code, 401)

    def test_rotated_keys_are_fetched_and_jwks_is_cached(self):
        self.exchange(self.token(), self.invite)
        self.exchange(self.token())
        self.assertEqual(self.fetches, 1, "keys are cached")
        new_private, new_jwk = keypair("k2")
        self.jwks["keys"].append(new_jwk)
        ni._jwks["fetched"] -= 60  # past the short refetch guard
        res = self.exchange(self.token(key=new_private, kid="k2"))
        self.assertEqual(res.json()["detail"]["code"], "invite_required", "verified with the rotated key")
        self.assertEqual(self.fetches, 2)

    def test_jwks_outage_is_a_503_not_a_login(self):
        def down(url):
            raise RuntimeError("network")
        with patch.object(ni, "_fetch_jwks", down):
            self.assertEqual(self.exchange(self.token(), self.invite).status_code, 503)


if __name__ == "__main__":
    unittest.main()
