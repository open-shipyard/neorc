// Dependencies and the checks below follow contributing/js-dependencies.md.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { gzipSync } from "node:zlib";

import react from "@vitejs/plugin-react";
import license from "rollup-plugin-license";
import { defineConfig, type Plugin } from "vite";

/** Licenses a bundled package may carry; the build fails on any other. */
const ALLOWED_LICENSES = [
  "MIT",
  "ISC",
  "0BSD",
  "BSD-2-Clause",
  "BSD-3-Clause",
  "Apache-2.0",
  "CC0-1.0",
  "Unlicense",
];

/** Everything shipped, gzipped, must fit in this. */
const SIZE_BUDGET_BYTES = 500 * 1024;

const here = import.meta.dirname;

/** What ends up in the bundle, one line per package, committed and checked by CI. */
const BUNDLED_PACKAGES = resolve(here, "bundled-packages.txt");

function gzippedSize(dir: string): number {
  let total = 0;
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      total += gzippedSize(path);
    } else {
      total += gzipSync(readFileSync(path)).length;
    }
  }
  return total;
}

function sizeBudget(): Plugin {
  return {
    name: "neorc-size-budget",
    apply: "build",
    closeBundle() {
      const size = gzippedSize(resolve(here, "dist"));
      if (size > SIZE_BUDGET_BYTES) {
        throw new Error(
          `the built assets are ${size} bytes gzipped, over the budget of ` +
            `${SIZE_BUDGET_BYTES}: see contributing/js-dependencies.md`,
        );
      }
    },
  };
}

export default defineConfig({
  // Relative asset paths, so the app works under any prefix, such as /ui/.
  base: "./",
  plugins: [react(), sizeBudget()],
  server: {
    // `npm run dev` against a local `neorc manager start`. The pattern is
    // matched against the URL with its query string, so `/runs?flow=a` and
    // `/runs/<id>` both reach the manager.
    proxy: {
      "^/(flows|runs|tasks|events|queues|health)([/?].*)?$":
        "http://127.0.0.1:8420",
    },
  },
  build: {
    rollupOptions: {
      plugins: [
        license({
          thirdParty: {
            allow: {
              test: (dependency) =>
                ALLOWED_LICENSES.includes(dependency.license ?? ""),
              failOnUnlicensed: true,
              failOnViolation: true,
            },
            output: [
              { file: resolve(here, "dist/THIRD_PARTY_LICENSES.txt") },
              {
                file: BUNDLED_PACKAGES,
                template: (dependencies) =>
                  [
                    "# Packages in the built bundle, written by the build.",
                    "# A change here is reviewed: see contributing/js-dependencies.md.",
                    ...dependencies
                      .map((d) => `${d.name} ${d.version} ${d.license}`)
                      .sort(),
                  ].join("\n") + "\n",
              },
            ],
          },
        }),
      ],
    },
  },
});
