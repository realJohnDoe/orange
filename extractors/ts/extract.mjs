// Run dependency-cruiser against one corpus checkout and emit its raw result as JSON.
//
// Deliberately a dumb adapter: no filtering, no path normalization, no counting. It
// emits exactly what dependency-cruiser saw so extract.py -- the only place with
// project imports and policy -- can decide what's internal, external, or unresolved.
// This split is what makes extract.py's build_graph() testable from a synthetic dict
// with no node in the loop.
//
// Usage: node extract.mjs <checkoutRoot> <root> [<root> ...]
// Output: JSON on stdout -- { "modules": [{ source, dependencies: [...] }] }

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { cruise } from "dependency-cruiser";

function packageAliasFor(checkoutRoot, root) {
  // Generic rule (not zod-specific): if `root` sits under a directory with its own
  // package.json, alias that package's name to `root` so self-referential imports
  // like `zod/v4` resolve via plain directory-index resolution -- the same mechanism
  // monorepo packages will need later. Silently skips if there's no package.json or
  // no "name" field; not every root has one (see date-fns, which doesn't need this).
  try {
    const pkg = JSON.parse(readFileSync(join(checkoutRoot, root, "..", "package.json"), "utf8"));
    if (pkg.name) return { [pkg.name]: join(checkoutRoot, root) };
  } catch {
    // no package.json next to this root, or it has no "name" -- nothing to alias
  }
  return {};
}

async function main() {
  const [checkoutRoot, ...roots] = process.argv.slice(2);
  if (!checkoutRoot || roots.length === 0) {
    process.stderr.write("usage: extract.mjs <checkoutRoot> <root> [<root> ...]\n");
    process.exit(1);
  }

  let alias = {};
  for (const root of roots) {
    alias = { ...alias, ...packageAliasFor(checkoutRoot, root) };
  }

  const result = await cruise(
    // relative to baseDir below -- dependency-cruiser joins these with baseDir
    // unconditionally (path.join doesn't special-case an absolute 2nd arg), so
    // passing already-absolute paths here would double the checkoutRoot prefix.
    roots,
    {
      baseDir: checkoutRoot,
      doNotFollow: { path: "node_modules" },
      tsPreCompilationDeps: true,
      moduleSystems: ["es6", "cjs"],
    },
    {
      alias,
      extensionAlias: { ".js": [".ts", ".tsx", ".js"] },
    },
  );

  const modules = result.output.modules.map((mod) => ({
    source: mod.source,
    dependencies: (mod.dependencies ?? []).map((dep) => ({
      resolved: dep.resolved,
      typeOnly: (dep.dependencyTypes ?? []).includes("type-only"),
      couldNotResolve: Boolean(dep.couldNotResolve),
      coreModule: Boolean(dep.coreModule),
    })),
  }));

  process.stdout.write(JSON.stringify({ modules }));
}

main();
