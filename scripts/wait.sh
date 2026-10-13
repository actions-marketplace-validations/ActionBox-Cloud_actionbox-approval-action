#!/usr/bin/env bash
set -euo pipefail

api_url=https://api.actionbox.cloud
if [[ ! "${ACTIONBOX_TIMEOUT:-}" =~ ^[1-9][0-9]{0,4}$ ]] ||
   (( 10#$ACTIONBOX_TIMEOUT > 21600 )); then
  echo "timeout must be an integer from 1 to 21600 seconds" >&2
  exit 2
fi
timeout_seconds=$((10#$ACTIONBOX_TIMEOUT))
deadline=$(( $(date +%s) + timeout_seconds ))
pending=true

# A cancelled or timed-out job should not leave an approval in the inbox.
# The API call is best effort because the runner might lose network access.
cleanup() {
  if [[ "$pending" == true && -n "${ACTIONBOX_ACTION_ID:-}" ]]; then
    curl --fail --silent --show-error --connect-timeout 5 --max-time 10 \
      --request POST "$api_url/v1/actions/$ACTIONBOX_ACTION_ID/cancel" \
      --header "Authorization: Bearer $ACTIONBOX_TOKEN" \
      --header 'Content-Type: application/json' \
      --data '{"reason":"GitHub Actions stopped waiting"}' >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
trap 'exit 1' INT TERM

while :; do
  remaining=$(( deadline - $(date +%s) ))
  if (( remaining <= 0 )); then
    break
  fi
  request_timeout=$(( remaining < 45 ? remaining : 45 ))
  wait_budget=$(( request_timeout > 5 ? request_timeout - 5 : 0 ))
  wait_seconds=$(( wait_budget < 30 ? wait_budget : 30 ))
  connect_timeout=$(( remaining < 10 ? remaining : 10 ))
  response="$(curl --fail-with-body --silent --show-error \
    --connect-timeout "$connect_timeout" --max-time "$request_timeout" \
    "$api_url/v1/source/actions/$ACTIONBOX_ACTION_ID?wait_seconds=$wait_seconds" \
    --header "Authorization: Bearer $ACTIONBOX_TOKEN")"
  status="$(jq -er '.data.status' <<<"$response")"
  if [[ "$status" == open ]]; then
    if (( wait_seconds == 0 )); then
      sleep 1
    fi
    continue
  fi
  pending=false
  decision="$(jq -r '.data.resolution_option_id // empty' <<<"$response")"
  resolved_by="$(jq -r '.data.resolved_by_type // empty' <<<"$response")"
  resolved_version="$(jq -r '.data.action_version // empty' <<<"$response")"
  resolved_fingerprint="$(jq -r '.data.fingerprint // empty' <<<"$response")"
  if [[ "$status" != resolved || "$resolved_by" != user ||
        "$resolved_version" != "$ACTIONBOX_ACTION_VERSION" ||
        "$resolved_fingerprint" != "$ACTIONBOX_FINGERPRINT" ]]; then
    echo "Approval ended without a human decision on the original request." >&2
    exit 1
  fi
  if [[ "$decision" != approve ]]; then
    echo "The human reviewer did not approve this workflow." >&2
    exit 1
  fi
  {
    echo "decision=$decision"
    echo "status=$status"
  } >>"$GITHUB_OUTPUT"
  exit 0
done

echo "No human decision arrived before the ActionBox timeout." >&2
exit 1
