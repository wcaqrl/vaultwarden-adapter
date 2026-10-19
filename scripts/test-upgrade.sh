#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."

: "${LAZYCAT_SOURCE_VERSION:?The selected stable Git tag version is required}"
[[ "$LAZYCAT_SOURCE_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
PLATFORM="${LAZYCAT_TARGET_PLATFORM:-linux/amd64}"
[[ "$PLATFORM" == linux/amd64 ]]
NEW_TAG="docker.io/vaultwarden/server:${LAZYCAT_SOURCE_VERSION}"
# This immutable amd64 digest equals the origin_hash in the approved 1.37.0
# LazyCat LPK. Use the official source instead of relying on anonymous access
# to the old LazyCat registry mirror from a GitHub-hosted runner.
OLD_IMAGE='docker.io/vaultwarden/server:1.37.0@sha256:f73331a80bdc497903ba3c375f556cefb4d40a5af156989beae79a58aba3e582'
TEST_ROOT="$(mktemp -d -t vaultwarden-upgrade.XXXXXXXX)"
CONTAINER_NAME="vaultwarden-upgrade-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}-$$"
PROOF=.lazycat-action/upgrade-proof.json
mkdir -p .lazycat-action "$TEST_ROOT/data"
rm -f -- "$PROOF"

cleanup() {
  docker rm --force "$CONTAINER_NAME" >/dev/null 2>&1 || true
  rm -rf -- "$TEST_ROOT"
}
trap cleanup EXIT
on_error() {
  echo 'Vaultwarden upgrade test failed; no upgrade proof was issued.' >&2
  # Keep test passwords and authentication tokens out of Actions logs.
  docker logs --tail 60 "$CONTAINER_NAME" >&2 2>/dev/null || true
}
trap on_error ERR

echo "Pulling the approved store baseline and official $LAZYCAT_SOURCE_VERSION image"
docker pull --platform "$PLATFORM" "$OLD_IMAGE" >/dev/null
docker pull --platform "$PLATFORM" "$NEW_TAG" >/dev/null
# The Docker Hub API returns the same selected-platform digest as the Action;
# do not confuse this with the multi-architecture index or config digest.
DIGEST="$(python3 - "$LAZYCAT_SOURCE_VERSION" <<'PY'
import json, re, sys, urllib.request
url = 'https://hub.docker.com/v2/namespaces/vaultwarden/repositories/server/tags/' + sys.argv[1]
with urllib.request.urlopen(url, timeout=30) as response:
    document = json.load(response)
images = [x for x in document['images'] if x.get('os') == 'linux' and x.get('architecture') == 'amd64']
if len(images) != 1 or not re.fullmatch(r'sha256:[a-f0-9]{64}', images[0]['digest']):
    raise SystemExit('Expected exactly one linux/amd64 image for the selected version')
print(images[0]['digest'])
PY
)"
NEW_IMAGE="$NEW_TAG@$DIGEST"
docker pull --platform "$PLATFORM" "$NEW_IMAGE" >/dev/null
python3 - "$TEST_ROOT" <<'PY'
import json, pathlib, secrets, sys
root = pathlib.Path(sys.argv[1])
(root / 'admin-token').write_text(secrets.token_urlsafe(32))
(root / 'admin-token').chmod(0o600)
# An actual non-default persisted setting must survive upgrades too.
(root / 'data/config.json').write_text(json.dumps({'signups_allowed': True, 'sends_allowed': False}))
PY

start_vaultwarden() {
  local image="$1" port deadline
  docker rm --force "$CONTAINER_NAME" >/dev/null 2>&1 || true
  docker run --detach --name "$CONTAINER_NAME" --platform "$PLATFORM" \
    --user "$(id -u):$(id -g)" --memory 512m --cpus 2 \
    --publish 127.0.0.1::80 \
    --env DOMAIN=http://localhost \
    --env SIGNUPS_ALLOWED=true \
    --env "ADMIN_TOKEN=$(cat "$TEST_ROOT/admin-token")" \
    --volume "$TEST_ROOT/data:/data" \
    "$image" >/dev/null
  port="$(docker port "$CONTAINER_NAME" 80/tcp | awk -F: 'NR == 1 {print $NF}')"
  deadline=$((SECONDS + 120))
  while (( SECONDS < deadline )); do
    if curl --fail --silent "http://127.0.0.1:$port/alive" >/dev/null; then
      curl --fail --silent "http://127.0.0.1:$port/" >/dev/null
      printf '%s\n' "$port"
      return 0
    fi
    [[ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER_NAME")" == true ]] || return 1
    sleep 2
  done
  return 1
}

echo 'Creating a test account, encrypted vault entry and encrypted attachment on 1.37.0'
port="$(start_vaultwarden "$OLD_IMAGE")"
python3 -B scripts/vault-fixture.py seed "http://127.0.0.1:$port" "$TEST_ROOT"
docker stop --time 20 "$CONTAINER_NAME" >/dev/null
python3 -B scripts/vault-fixture.py snapshot "$TEST_ROOT"

echo "Verifying data migration to $LAZYCAT_SOURCE_VERSION ($DIGEST)"
port="$(start_vaultwarden "$NEW_IMAGE")"
python3 -B scripts/vault-fixture.py verify "http://127.0.0.1:$port" "$TEST_ROOT"
docker stop --time 20 "$CONTAINER_NAME" >/dev/null
python3 -B scripts/vault-fixture.py compare "$TEST_ROOT"

echo 'Recreating the upgraded container and verifying login, sync and attachment decryption again'
port="$(start_vaultwarden "$NEW_IMAGE")"
python3 -B scripts/vault-fixture.py verify "http://127.0.0.1:$port" "$TEST_ROOT"
docker stop --time 20 "$CONTAINER_NAME" >/dev/null
python3 -B scripts/vault-fixture.py compare "$TEST_ROOT"

# Only successful full tests produce a proof. This file contains image metadata
# only, never account data, keys, passwords or tokens, and is not packaged.
python3 - "$PROOF" "$LAZYCAT_SOURCE_VERSION" "${LAZYCAT_APPLICATION_VERSION:-$LAZYCAT_SOURCE_VERSION}" "$DIGEST" <<'PY'
import json, pathlib, sys
path, source, application, digest = sys.argv[1:]
pathlib.Path(path).write_text(json.dumps({
    'source_version': source, 'application_version': application, 'digest': digest,
    'checks': ['login', 'decrypt', 'attachment', 'config', 'sqlite', 'restart'],
}, indent=2) + '\n')
PY
echo "Upgrade verified: 1.37.0 -> $LAZYCAT_SOURCE_VERSION; account, encrypted vault, attachment, config, keys and SQLite integrity survived recreation."
