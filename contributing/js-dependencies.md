# JavaScript dependencies

Rules for the packages under `ts/`. Their code ships to users inside a wheel,
compiled into a bundle nobody reads, and runs in a browser against an API
that has no authentication yet. Every dependency is code the project vouches
for, at every version it takes.

## Keep them few

- Add a runtime dependency only when writing the code would cost more than
  vetting the package, and its transitive tree, now and at every update.
- `dependencies` is what the bundle ships; `devDependencies` is build tooling.
  Keep the first list as short as it can be.
- `bundled-packages.txt`, committed next to the package, lists every package
  that ends up in the bundle. The build writes it and CI fails when it
  differs, so a new bundled package is a line in the pull request diff.

## Vet before adding

- Maintained: recent releases, issues answered, more than one maintainer.
- Small: `npm view <pkg> dependencies` is short, and the size fits the budget.
- Licensed on the allowlist below.
- Published with provenance, `npm view <pkg> dist.attestations`.
- Needs no install script. A package that does is a reason to pick another.
- Read what npm serves, `npm pack <pkg>` and look inside; the GitHub
  repository is not necessarily what was published.

## Licenses

Bundled packages may be licensed MIT, ISC, 0BSD, BSD-2-Clause, BSD-3-Clause,
Apache-2.0, CC0-1.0 or Unlicense. The build fails on anything else, and on a
package with no recognised license (`rollup-plugin-license` in
`vite.config.ts`). The wheel is Apache-2.0; a copyleft library in the bundle
would put that in question. Extending the list is a reviewed change with a
stated reason. Build tooling is not distributed and is not checked.

## Installing and updating

- Exact versions in `package.json`, no ranges. `package-lock.json` is
  committed and installs are `npm ci`, never `npm install`.
- `.npmrc` sets `ignore-scripts=true`; install scripts do not run on any
  machine or in CI.
- Updates come from Dependabot, with a cooldown: no version published fewer
  than 10 days ago. Do not update by hand to a version younger than that;
  compromised releases are usually withdrawn within days.
- One dependency change per pull request, never as a side effect of a feature.
- CI runs `npm audit` for known advisories and `npm audit signatures` for
  registry signatures and provenance.
- Node.js at the version in `.nvmrc`, installed as
  [dev-environment.md](dev-environment.md) says.

## In the browser

- The manager serves `/ui/` with a Content-Security-Policy that allows
  scripts, styles and connections from its own origin only. Do not loosen it
  for a library; choose another library.
- No CDN and no request outside the manager's API. Every asset is in the
  wheel, so the UI works on air-gapped hosts.

## Releases

Bundles are built in CI on the release tag, from the committed lockfile and
`.nvmrc`. Built assets are never committed, and a bundle built on a laptop is
never released.
