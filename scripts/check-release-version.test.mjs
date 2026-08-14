import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  assertReleaseVersion,
  readReleaseVersions,
  releaseVersionFromTag,
} from "./check-release-version.mjs";

test("releaseVersionFromTag accepts semantic release tags", () => {
  assert.equal(releaseVersionFromTag("v3.2.0"), "3.2.0");
  assert.equal(releaseVersionFromTag("v4.0.0-rc.1"), "4.0.0-rc.1");
});

test("releaseVersionFromTag rejects malformed tags", () => {
  assert.throws(() => releaseVersionFromTag("3.2.0"), /leading v/);
  assert.throws(() => releaseVersionFromTag("latest"), /semantic versioning/);
});

test("assertReleaseVersion rejects a mismatched source", () => {
  assert.throws(
    () => assertReleaseVersion("v3.2.0", {
      "package.json": "3.2.0",
      "src-tauri/Cargo.toml": "3.1.0",
    }),
    /src-tauri\/Cargo\.toml: 3\.1\.0/,
  );
});

test("readReleaseVersions reads every release version source", () => {
  const root = mkdtempSync(join(tmpdir(), "conversation-reclaim-version-"));
  mkdirSync(join(root, "src-tauri"));
  writeFileSync(join(root, "package.json"), JSON.stringify({ version: "5.1.0" }));
  writeFileSync(join(root, "package-lock.json"), JSON.stringify({ version: "5.1.0", packages: { "": { version: "5.1.0" } } }));
  writeFileSync(join(root, "src-tauri/tauri.conf.json"), JSON.stringify({ version: "5.1.0" }));
  writeFileSync(join(root, "src-tauri/Cargo.toml"), "[package]\nname = \"conversation-reclaim\"\nversion = \"5.1.0\"\n\n[dependencies]\nserde = \"1\"\n");

  assert.deepEqual(readReleaseVersions(root), {
    "package.json": "5.1.0",
    "package-lock.json": "5.1.0",
    "package-lock.json packages root": "5.1.0",
    "src-tauri/Cargo.toml": "5.1.0",
    "src-tauri/tauri.conf.json": "5.1.0",
  });
});
