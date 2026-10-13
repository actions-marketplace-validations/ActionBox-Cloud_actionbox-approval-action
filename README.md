# ActionBox Human Approval Gate for GitHub Actions

Ask a person to approve a workflow step through [ActionBox](https://actionbox.cloud) before it continues. The action creates a two-choice review request, waits for the decision, and succeeds only when a human selects **Approve** on the unchanged request. Rejection, expiration, cancellation, timeout, API failure, and a changed request fail the step.

This repository contains only the integration wrapper. ActionBox is a proprietary hosted service; this action calls `https://api.actionbox.cloud`. It does not install or deploy the ActionBox service.

## Use it in a workflow

1. Create a Source in the [ActionBox dashboard](https://actionbox.cloud/sources) and copy its Source key.
2. Save that key as the GitHub Actions repository secret `ACTIONBOX_SOURCE_KEY`.
3. Add the gate after the checks that should run before review. Use a trusted event such as `workflow_dispatch`; do not expose the Source key to untrusted pull-request code.

Use the published `v1` action in your workflow:

```yaml
- name: Request approval
  id: approval
  uses: ActionBox-Cloud/actionbox-approval-action@v1
  with:
    token: ${{ secrets.ACTIONBOX_SOURCE_KEY }}
    title: Approve production deployment
    description: Review commit ${{ github.sha }} before deployment.
    context-json: '[{"type":"key_value","items":{"environment":"production","commit":"${{ github.sha }}"}}]'
    timeout: "3600"

- name: Continue after approval
  if: steps.approval.outputs.decision == 'approve'
  run: echo "Approved. Replace this step with your deployment command."
```

The [complete example](examples/deploy.yml) starts on `workflow_dispatch` and ends with a harmless echo. Replace that final step with your deployment command. Pin the action to a reviewed full commit SHA when your repository requires immutable dependencies.

## Inputs and outputs

| Input | Required | Meaning |
| --- | --- | --- |
| `token` | Yes | Source key from a GitHub Actions secret |
| `title` | Yes | Short request shown to the reviewer |
| `description` | No | Additional context |
| `context-json` | No | JSON array of typed [context blocks](https://actionbox.cloud/docs/context-blocks) |
| `timeout` | No | Seconds to wait; 1–21600, default 3600 |

On success, `action-id` is the ActionBox Action ID, `decision` is `approve`, and `status` is `resolved`. The action fails on every other outcome. It requires `bash`, `curl`, `jq`, and `openssl` on the runner; GitHub-hosted Ubuntu runners include them. A self-managed runner must provide them.

Each invocation creates a separate review request, including parallel matrix jobs and reruns. If the initial API response is lost, the action retries once with the same request and idempotency key so the retry does not create a duplicate. A later invocation uses a new key; an earlier request may remain open until expiry if its runner stopped before cleanup. The request links back to the workflow run and expires server-side two minutes after the configured wait. The action checks the Action version and fingerprint returned at creation before accepting a decision, so a materially changed request cannot authorize this step.

## When to use ActionBox

GitHub [environment required reviewers](https://docs.github.com/en/actions/concepts/workflows-and-actions/deployment-environments) already provide a native deployment approval gate and are often the simplest choice. ActionBox is useful when a team wants a hosted decision inbox shared with other workflows, review context in the request, or a decision that a non-GitHub worker can read through the ActionBox API. Evaluate those needs against the extra integration and service dependency.

This composite action **keeps a runner occupied while it waits**. GitHub environment approvals wait before a runner is assigned. Account for runner minutes and the job timeout when choosing a long wait. The action makes a best-effort cancellation request if the wait ends early; the server-side expiry covers a runner that cannot send that request.

## Availability

This folder is the canonical source for the [public ActionBox GitHub Action](https://github.com/ActionBox-Cloud/actionbox-approval-action), which is also listed on GitHub Marketplace. Use `@v1` for the published major version or pin a reviewed full commit SHA. The integration wrapper is MIT-licensed; ActionBox remains a proprietary hosted service.
