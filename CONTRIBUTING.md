# Contributing

Issues and pull requests for this integration wrapper are welcome. Please never
include Source keys, customer review data, or private workflow details.

Maintainers review proposed changes and may ask for revisions before a release.

Run the credential-free tests before submitting a behavior change:

```sh
python3 -m unittest discover -s tests -v
```

The tests mock API calls. They require `bash`, `jq`, and `openssl`; no
ActionBox account is needed.
