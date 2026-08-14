import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const RELEASE_TAG_PATTERN = /^v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;

export function releaseVersionFromTag(tag) {
  if (!tag || !RELEASE_TAG_PATTERN.test(tag)) {
    throw new Error(`Release tag must use semantic versioning with a leading v (received: ${tag ?? "<missing>"})`);
  }
  return tag.slice(1);
}

function cargoPackageVersion(contents) {
  const packageHeader = contents.indexOf("[package]");
  if (packageHeader === -1) return undefined;
  const afterHeader = contents.slice(packageHeader + "[package]".length);
  const nextSection = afterHeader.search(/\n\s*\[/);
  const packageSection = nextSection === -1 ? afterHeader : afterHeader.slice(0, nextSection);
  return packageSection.match(/^\s*version\s*=\s*"([^"]+)"\s*$/m)?.[1];
}

export function readReleaseVersions(root = process.cwd()) {
  const packageJson = JSON.parse(readFileSync(resolve(root, "package.json"), "utf8"));
  const packageLock = JSON.parse(readFileSync(resolve(root, "package-lock.json"), "utf8"));
  const tauri = JSON.parse(readFileSync(resolve(root, "src-tauri/tauri.conf.json"), "utf8"));
  const cargo = readFileSync(resolve(root, "src-tauri/Cargo.toml"), "utf8");

  return {
    "package.json": packageJson.version,
    "package-lock.json": packageLock.version,
    "package-lock.json packages root": packageLock.packages?.[""]?.version,
    "src-tauri/Cargo.toml": cargoPackageVersion(cargo),
    "src-tauri/tauri.conf.json": tauri.version,
  };
}

export function assertReleaseVersion(tag, versions) {
  const expected = releaseVersionFromTag(tag);
  const mismatches = Object.entries(versions)
    .filter(([, version]) => version !== expected)
    .map(([source, version]) => `${source}: ${version ?? "<missing>"}`);

  if (mismatches.length > 0) {
    throw new Error([
      `Release ${tag} does not match every declared application version.`,
      `Expected: ${expected}`,
      ...mismatches,
    ].join("\n"));
  }

  return expected;
}

export function main() {
  const versions = readReleaseVersions();
  const currentOnly = process.argv.includes("--current");
  const tag = currentOnly ? `v${versions["package.json"]}` : process.env.RELEASE_TAG;
  const version = assertReleaseVersion(tag, versions);
  console.log(`Release version ${version} is consistent across ${Object.keys(versions).length} sources.`);
}

const directPath = process.argv[1] ? resolve(process.argv[1]) : "";
if (directPath === fileURLToPath(import.meta.url)) {
  try {
    main();
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}
