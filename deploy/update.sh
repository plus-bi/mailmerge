#!/usr/bin/env bash
set -Eeuo pipefail

readonly APP_DIR="/opt/mailmerge"
readonly RUNTIME_ENV="${APP_DIR}/.env"
readonly FRONTEND_ENV="${APP_DIR}/frontend/.env.production.local"
readonly PUBLIC_URL="https://mailmerge.plus.bi"
readonly DEPLOY_REF="${1:-}"
readonly SERVICES=(mailmerge.service mailmerge-worker.service)

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

if [[ "${EUID}" -eq 0 ]]; then
  fail "run this script as the deployment user, not root"
fi

[[ -d "${APP_DIR}/.git" ]] || fail "${APP_DIR} is not a Git checkout"
[[ -f "${RUNTIME_ENV}" ]] || fail "missing ${RUNTIME_ENV}"

cd "${APP_DIR}"

if [[ -n "$(git status --porcelain)" ]]; then
  fail "the deployment checkout has local changes; reconcile them before updating"
fi

previous_commit="$(git rev-parse HEAD)"
git fetch --prune origin

if [[ -n "${DEPLOY_REF}" ]]; then
  git rev-parse --verify "${DEPLOY_REF}^{commit}" >/dev/null 2>&1 || \
    fail "commit or ref '${DEPLOY_REF}' was not found after fetching origin"
  target_commit="$(git rev-parse "${DEPLOY_REF}^{commit}")"
  git merge --ff-only "${target_commit}"
  [[ "$(git rev-parse HEAD)" == "${target_commit}" ]] || \
    fail "the current branch is already ahead of ${DEPLOY_REF}; refusing to deploy a different commit"
else
  current_branch="$(git symbolic-ref --quiet --short HEAD)" || \
    fail "the checkout is detached; pass the commit or ref to deploy"
  git pull --ff-only origin "${current_branch}"
fi

echo "Updating Python dependencies..."
source "${APP_DIR}/.venv/bin/activate"
python -m pip install -e .
python -m compileall -q src

clerk_publishable_key="$(python -c 'from dotenv import dotenv_values; values = dotenv_values(".env"); print(values.get("VITE_CLERK_PUBLISHABLE_KEY") or values.get("CLERK_PUBLISHABLE_KEY") or "")')"
[[ -n "${clerk_publishable_key}" ]] || \
  fail "set CLERK_PUBLISHABLE_KEY or VITE_CLERK_PUBLISHABLE_KEY in ${RUNTIME_ENV}"
configured_data_dir="$(python -c 'from dotenv import dotenv_values; print(dotenv_values(".env").get("MAILMERGE_DATA_DIR") or "")')"

printf 'VITE_CLERK_PUBLISHABLE_KEY=%s\n' "${clerk_publishable_key}" > "${FRONTEND_ENV}"
chmod 600 "${FRONTEND_ENV}"

echo "Building frontend..."
cd "${APP_DIR}/frontend"
npm ci
npm run lint
npm run build

frontend_target="${configured_data_dir:-${HOME}/.local/share/mailmerge}/frontend"
mkdir -p "${frontend_target}"
cp -a dist/. "${frontend_target}/"

echo "Restarting services..."
systemctl --user daemon-reload
declare -A previous_pids
for service in "${SERVICES[@]}"; do
  previous_pids["${service}"]="$(systemctl --user show "${service}" --property=MainPID --value 2>/dev/null || true)"
done
systemctl --user restart --no-block "${SERVICES[@]}"

for service in "${SERVICES[@]}"; do
  for _attempt in {1..120}; do
    state="$(systemctl --user is-active "${service}" 2>/dev/null || true)"
    current_pid="$(systemctl --user show "${service}" --property=MainPID --value 2>/dev/null || true)"
    if [[ "${state}" == "active" && "${current_pid}" != "0" && "${current_pid}" != "${previous_pids[${service}]}" ]]; then
      break
    fi
    [[ "${state}" == "failed" ]] && break
    sleep 1
  done
done

echo "Service status:"
status_code=0
timeout 15s systemctl --user --no-pager status "${SERVICES[@]}" || status_code=$?
if [[ "${status_code}" -ne 0 ]]; then
  echo "systemctl status did not report success (exit ${status_code})." >&2
  systemctl --user --no-pager is-active "${SERVICES[@]}" || true
  journalctl --user -u mailmerge.service -u mailmerge-worker.service -n 100 --no-pager
  exit 1
fi

ui_status="000"
api_status="000"
for _attempt in {1..30}; do
  ui_status="$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 "${PUBLIC_URL}/" || true)"
  api_status="$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 "${PUBLIC_URL}/api/v1/profiles" || true)"
  [[ "${ui_status}" == "200" && "${api_status}" == "401" ]] && break
  sleep 2
done
[[ "${ui_status}" == "200" ]] || fail "public UI health check returned HTTP ${ui_status}"
[[ "${api_status}" == "401" ]] || fail "public unauthenticated API check returned HTTP ${api_status}, expected 401"

if [[ -f "${APP_DIR}/deploy/.env" ]] && \
   ! git diff --quiet "${previous_commit}" HEAD -- deploy/ src/unsubscribe_service pyproject.toml; then
  echo "Containerized services changed; rebuilding the Compose stack..."
  docker compose --project-directory "${APP_DIR}/deploy" up -d --build --remove-orphans
  docker compose --project-directory "${APP_DIR}/deploy" ps
fi

echo "Deployed commit $(git rev-parse --short HEAD)."
