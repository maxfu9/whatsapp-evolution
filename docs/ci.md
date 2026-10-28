# Quality checks

`Pylint` runs on Python 3.14 with the real, pinned Frappe v16 and ERPNext
dependencies. It does not need a site, a database, or production credentials.
The tools are pinned in `ci_tools/requirements.txt`.

All Pylint categories run. Every error/fatal diagnostic fails the build.
Existing convention, warning and refactor findings are recorded by file and
symbol in `ci_tools/pylint-baseline.json`. An increase in any budget fails too;
line movement does not affect the comparison. The complete JSON report is
uploaded even when the job fails. This baseline exposes the legacy style debt
without pretending it was fixed or requiring an unrelated repository-wide
reformat. New code should introduce no extra lint findings.

The small Astroid plugin reads each DocType controller's sibling JSON and
exposes only its declared fields. It does not disable `no-member`; a regression
test ensures misspelled fields still fail.

Local commands, using an environment with Frappe/ERPNext installed:

```sh
python -m pip install -r ci_tools/requirements.txt
FRAPPE_STREAM_LOGGING=1 python -m ci_tools.lint
FRAPPE_STREAM_LOGGING=1 python -m unittest ci_tools.test_quality
```

Baseline updates must be deliberate, reviewed changes:

```sh
python -m ci_tools.lint --update-baseline
```

CI never updates the baseline, and errors cannot be added to it. The separate
`CI` workflow performs fresh bench/site installation and integration tests.
