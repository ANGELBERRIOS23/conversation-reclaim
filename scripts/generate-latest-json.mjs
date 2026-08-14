import { readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { releaseVersionFromTag } from "./check-release-version.mjs";

export const PLATFORM_ARTIFACTS = {
  "windows-x86_64": "Conversation-Reclaim-Windows-x64-Setup.exe",
  "darwin-aarch64": "Conversation-Reclaim-macOS-arm64.app.tar.gz",
  "darwin-x86_64": "Conversation-Reclaim-macOS-x64.app.tar.gz",
};

export function buildUpdaterMetadata({ directory, tag, repository, notes, pubDate = new Date().toISOString() }) {
  if (!repository) throw new Error("RELEASE_REPOSITORY is required");
  const version = releaseVersionFromTag(tag);
  const baseUrl = `https://github.com/${repository}/releases/download/${tag}`;

  return {
    version,
    notes: notes?.trim() || `Conversation Reclaim ${tag}`,
    pub_date: pubDate,
    platforms: Object.fromEntries(Object.entries(PLATFORM_ARTIFACTS).map(([platform, filename]) => [
      platform,
      {
        url: `${baseUrl}/${filename}`,
        signature: readFileSync(join(directory, `${filename}.sig`), "utf8").trim(),
      },
    ])),
  };
}

export function main() {
  const directory = process.argv[2] ?? "artifacts";
  const tag = process.env.RELEASE_TAG;
  const repository = process.env.RELEASE_REPOSITORY;
  const notes = process.env.RELEASE_NOTES_FILE
    ? readFileSync(process.env.RELEASE_NOTES_FILE, "utf8")
    : undefined;

  const metadata = buildUpdaterMetadata({ directory, tag, repository, notes });
  writeFileSync(join(directory, "latest.json"), `${JSON.stringify(metadata, null, 2)}\n`);
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
