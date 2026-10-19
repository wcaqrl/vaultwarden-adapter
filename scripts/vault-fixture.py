"""Exercise the real Vaultwarden API using disposable, client-encrypted data.

Uses Python's standard library and the runner's OpenSSL, no third-party client
or production credentials. The encType 2 encryption matches Bitwarden's
AES-256-CBC / HMAC-SHA256 format, so we verify decryption as well as byte survival.
"""
import base64
import hashlib
import hmac
import http.cookiejar
import json
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


def b64(value):
    return base64.b64encode(value).decode()


def unb64(value):
    return base64.b64decode(value)


def aes(value, key, iv, decrypt=False):
    args = ['openssl', 'enc', '-aes-256-cbc', '-K', key.hex(), '-iv', iv.hex()]
    if decrypt:
        args.append('-d')
    return subprocess.run(args, input=value, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout


def encrypt(value, key):
    iv = os.urandom(16)
    ciphertext = aes(value, key[:32], iv)
    mac = hmac.digest(key[32:], iv + ciphertext, 'sha256')
    return '2.' + '|'.join(map(b64, (iv, ciphertext, mac)))


def decrypt(value, key):
    assert value.startswith('2.'), 'Unexpected encryption type'
    iv, ciphertext, mac = map(unb64, value[2:].split('|'))
    assert hmac.compare_digest(mac, hmac.digest(key[32:], iv + ciphertext, 'sha256')), 'Encrypted data authentication failed'
    return aes(ciphertext, key[:32], iv, decrypt=True)


def normalize(value):
    if isinstance(value, dict):
        return {k.lower(): normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return value


class API:
    def __init__(self, url):
        assert url.startswith('http://127.0.0.1:'), 'Fixture API must be local to the runner'
        self.url = url
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        self.token = None

    def call(self, path, value=None, form=False, raw=False, content_type=None):
        headers = {'Bitwarden-Client-Name': 'web', 'Bitwarden-Client-Version': '2026.9.0'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if form:
            body = urllib.parse.urlencode(value).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        elif isinstance(value, bytes):
            body = value
            headers['Content-Type'] = content_type
        elif value is not None:
            body = json.dumps(value).encode()
            headers['Content-Type'] = 'application/json'
        else:
            body = None
        request = urllib.request.Request(self.url + path, data=body, headers=headers)
        try:
            with self.opener.open(request, timeout=30) as response:
                data = response.read()
        except urllib.error.HTTPError as error:
            # Log status/route only; HTTP response bodies can contain secrets.
            raise RuntimeError(f'Fixture API failed: {request.method} {path.split("?")[0]} HTTP {error.code}') from None
        return data if raw or not data else normalize(json.loads(data))

    def login(self, fixture):
        result = self.call('/identity/connect/token', {
            'grant_type': 'password', 'client_id': 'web', 'scope': 'api offline_access',
            'username': fixture['email'], 'password': fixture['password_hash'],
            'deviceIdentifier': fixture['device'], 'deviceName': 'LazyCat upgrade test', 'deviceType': '10',
        }, form=True)
        self.token = result['access_token']
        return result


def seed(api, root):
    email = 'lazycat-upgrade@example.invalid'
    password = b64(os.urandom(32))
    master_key = hashlib.pbkdf2_hmac('sha256', password.encode(), email.encode(), 600000)
    password_hash = b64(hashlib.pbkdf2_hmac('sha256', master_key, password.encode(), 1))
    stretched = b''.join(hmac.digest(master_key, label + b'\x01', 'sha256') for label in (b'enc', b'mac'))
    vault_key = os.urandom(64)
    wrapped = encrypt(vault_key, stretched)
    fixture = {'email': email, 'password_hash': password_hash, 'device': str(uuid.uuid4()),
               'vault_key': b64(vault_key), 'wrapped_key': wrapped}
    # The baseline still supports this legacy registration endpoint. Newer
    # versions removed it; upgraded-account login and sync use current APIs.
    api.call('/identity/accounts/register', {
        'email': email, 'name': 'LazyCat upgrade test', 'masterPasswordHash': password_hash,
        'key': wrapped, 'kdf': 0, 'kdfIterations': 600000,
    })
    api.login(fixture)
    cipher = api.call('/api/ciphers', {
        'type': 1, 'name': encrypt(b'Upgrade test vault entry', vault_key),
        'notes': encrypt(b'Encrypted note created on the approved store baseline', vault_key),
        'login': {'username': encrypt(b'upgrade-user', vault_key),
                  'password': encrypt(b'upgrade-fixture-password', vault_key)},
        'favorite': True,
    })
    fixture['cipher_id'] = cipher['id']
    attachment_key = os.urandom(64)
    encoded = encrypt(b'Encrypted attachment survives a Vaultwarden upgrade.', attachment_key)
    iv, ciphertext, mac = map(unb64, encoded[2:].split('|'))
    attachment = bytes([2]) + iv + mac + ciphertext
    fixture['attachment_key'] = b64(attachment_key)
    fixture['attachment_sha256'] = hashlib.sha256(attachment).hexdigest()
    uploaded = api.call(f"/api/ciphers/{cipher['id']}/attachment/v2", {
        'key': encrypt(attachment_key, vault_key),
        'fileName': encrypt(b'upgrade-test.txt', vault_key), 'fileSize': len(attachment),
    })
    fixture['attachment_id'] = uploaded['attachmentid']
    boundary = 'lazycat-' + uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="data"; filename="encrypted.bin"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n').encode() + attachment + f'\r\n--{boundary}--\r\n'.encode()
    api.call('/api' + uploaded['url'], body, raw=True, content_type='multipart/form-data; boundary=' + boundary)
    path = root / 'fixture.json'
    path.write_text(json.dumps(fixture))
    path.chmod(0o600)
    verify(api, root)
    print('Baseline account, encrypted vault item and encrypted attachment created and verified.')


def verify(api, root):
    fixture = json.loads((root / 'fixture.json').read_text())
    login = api.login(fixture)
    # Successful login must return the original encrypted user key.
    assert login['key'] == fixture['wrapped_key'], 'Wrapped user key changed'
    key = unb64(fixture['vault_key'])
    cipher = api.call('/api/ciphers/' + fixture['cipher_id'])
    assert decrypt(cipher['name'], key) == b'Upgrade test vault entry'
    assert decrypt(cipher['notes'], key) == b'Encrypted note created on the approved store baseline'
    assert decrypt(cipher['login']['username'], key) == b'upgrade-user'
    assert decrypt(cipher['login']['password'], key) == b'upgrade-fixture-password'
    synced = api.call('/api/sync?excludeDomains=true')
    assert any(x['id'] == fixture['cipher_id'] for x in synced['ciphers']), 'Cipher missing from client sync'
    attachment = api.call(f"/api/ciphers/{fixture['cipher_id']}/attachment/{fixture['attachment_id']}")
    assert decrypt(attachment['filename'], key) == b'upgrade-test.txt'
    attachment_key = decrypt(attachment['key'], key)
    assert attachment_key == unb64(fixture['attachment_key'])
    # The configured public domain differs from the runner's loopback port.
    # Request only the returned path/query on the same known local service.
    parts = urllib.parse.urlsplit(attachment['url'])
    blob = api.call(parts.path + ('?' + parts.query if parts.query else ''), raw=True)
    assert hashlib.sha256(blob).hexdigest() == fixture['attachment_sha256']
    assert blob[0] == 2
    encoded = '2.' + '|'.join(map(b64, (blob[1:17], blob[49:], blob[17:49])))
    assert decrypt(encoded, attachment_key) == b'Encrypted attachment survives a Vaultwarden upgrade.'
    config = json.loads((root / 'data/config.json').read_text())
    assert config['sends_allowed'] is False, 'Persisted administrator setting changed'
    api.call('/admin', {'token': (root / 'admin-token').read_text()}, form=True, raw=True)
    assert any(cookie.name == 'VW_ADMIN' for cookie in api.cookies), 'Original administrator Key no longer works'
    print('Verified login, client sync and decryption of the original vault entry and attachment.')


def disk_state(root):
    data = root / 'data'
    with sqlite3.connect('file:' + str(data / 'db.sqlite3') + '?mode=ro', uri=True) as database:
        assert database.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'SQLite integrity check failed'
        assert database.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 1
        assert database.execute('SELECT COUNT(*) FROM ciphers').fetchone()[0] == 1
        assert database.execute('SELECT COUNT(*) FROM attachments').fetchone()[0] == 1
    files = [data / 'config.json', *sorted(data.glob('rsa_key*')), *sorted((data / 'attachments').rglob('*'))]
    result = {str(p.relative_to(data)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}
    assert 'rsa_key.pem' in result, 'RSA signing key missing'
    return result


if __name__ == '__main__':
    command = sys.argv[1]
    if command in ('seed', 'verify'):
        api, root = API(sys.argv[2]), Path(sys.argv[3])
        (seed if command == 'seed' else verify)(api, root)
    else:
        root = Path(sys.argv[2])
        state = disk_state(root)
        if command == 'snapshot':
            (root / 'disk-state.json').write_text(json.dumps(state))
        elif command == 'compare':
            assert state == json.loads((root / 'disk-state.json').read_text()), 'Persisted config, RSA keys or attachment bytes changed'
            print('SQLite integrity, account/item/attachment counts, persisted config and signing keys verified.')
        else:
            raise SystemExit('Unknown fixture command')
