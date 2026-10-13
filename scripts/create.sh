#!/usr/bin/env bash
set -euo pipefail

api_url=https://api.actionbox.cloud
if [[ ! "${ACTIONBOX_TIMEOUT:-}" =~ ^[1-9][0-9]{0,4}$ ]] ||
   (( 10#$ACTIONBOX_TIMEOUT > 21600 )); then
  echo "timeout must be an integer from 1 to 21600 seconds" >&2
  exit 2
fi
timeout_seconds=$((10#$ACTIONBOX_TIMEOUT))
if [[ -n "${ACTIONBOX_CONTEXT_JSON:-}" ]] &&
   ! jq -e 'type == "array"' <<<"$ACTIONBOX_CONTEXT_JSON" >/dev/null; then
  echo "context-json must be a JSON array" >&2
  exit 2
fi

# Each invocation gets its own key. GitHub's job/action identifiers are shared
# by parallel matrix cells, so they cannot safely identify a review request.
# Keep this key and the payload unchanged for a retry within this invocation.
idempotency_key="github-action-$(openssl rand -hex 16)"

payload="$(jq -n \
  --arg title "$ACTIONBOX_TITLE" \
  --arg description "${ACTIONBOX_DESCRIPTION:-}" \
  --arg context_raw "${ACTIONBOX_CONTEXT_JSON:-}" \
  --arg gh_repo "$GITHUB_REPOSITORY" \
  --arg gh_ref "${GITHUB_REF_NAME:-}" \
  --arg gh_sha "${GITHUB_SHA:-}" \
  --arg gh_workflow "${GITHUB_WORKFLOW:-}" \
  --arg gh_server "${GITHUB_SERVER_URL:-https://github.com}" \
  --arg gh_run "$GITHUB_RUN_ID" \
  --argjson timeout "$timeout_seconds" \
  '{
    title:$title,
    description:$description,
    expires_at:(now + $timeout + 120 | todateiso8601),
    options:[
      {id:"approve",label:"Approve",style:"primary"},
      {id:"reject",label:"Reject",style:"destructive"}
    ],
    metadata:{origin:{
      provider:"github_actions",
      ref:(($gh_workflow | if . == "" then "workflow" else . end | .[0:200]) + " · run " + $gh_run),
      url:($gh_server + "/" + $gh_repo + "/actions/runs/" + $gh_run),
      labels:([$gh_repo[0:120],$gh_ref[0:120],$gh_sha[0:7]] | map(select(. != "")))
    }}
  }
  | if $context_raw != "" then .context=($context_raw|fromjson) else . end')"

response=""
for attempt in 1 2; do
  if response="$(curl --fail-with-body --silent --show-error --connect-timeout 10 --max-time 45 \
    --request POST "$api_url/v1/actions" \
    --header "Authorization: Bearer $ACTIONBOX_TOKEN" \
    --header 'Content-Type: application/json' \
    --header "Idempotency-Key: $idempotency_key" \
    --data "$payload")"; then
    break
  fi
  if (( attempt == 2 )); then
    echo "Unable to create the ActionBox approval request." >&2
    exit 1
  fi
  sleep 1
done

action_id="$(jq -er '.data.id' <<<"$response")"
action_version="$(jq -er '.data.action_version' <<<"$response")"
fingerprint="$(jq -er '.data.fingerprint' <<<"$response")"
[[ "$action_id" =~ ^[A-Za-z0-9_-]{1,64}$ &&
   "$fingerprint" =~ ^sha256:[a-f0-9]{64}$ &&
   "$action_version" =~ ^[1-9][0-9]*$ ]] || {
  echo "ActionBox returned an invalid Action snapshot" >&2
  exit 1
}
{
  echo "action_id=$action_id"
  echo "action_version=$action_version"
  echo "fingerprint=$fingerprint"
} >>"$GITHUB_OUTPUT"
