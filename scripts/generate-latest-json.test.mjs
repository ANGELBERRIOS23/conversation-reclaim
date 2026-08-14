import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { buildUpdaterMetadata, PLATFORM_ARTIFACTS } from "./generate-latest-json.mjs";

test("buildUpdaterMetadata uses release notes and signed platform artifacts", () => {
  const directory = mkdtempSync(join(tmpdir(), "conversation-reclaim-updater-"));
  for (const filename of Object.values(PLATFORM_ARTIFACTS)) {
    writeFileSync(join(directory, `${filename}.sig`), `signature-for-${filename}\n`);
  }

  const metadata = buildUpdaterMetadata({
    directory,
    tag: "v3.3.0",
    repository: "owner/repo",
    notes: "Security and release hardening\n",
    pubDate: "2026-08-13T00:00:00.000Z",
  });

  assert.equal(metadata.version, "3.3.0");
  assert.equal(metadata.notes, "Security and release hardening");
  assert.equal(metadata.pub_date, "2026-08-13T00:00:00.000Z");
  assert.equal(metadata.platforms["windows-x86_64"].url, "https://github.com/owner/repo/releases/download/v3.3.0/Conversation-Reclaim-Windows-x64-Setup.exe");
  assert.equal(metadata.platforms["darwin-aarch64"].signature, "signature-for-Conversation-Reclaim-macOS-arm64.app.tar.gz");
});
