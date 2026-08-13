import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const directory = process.argv[2] ?? "artifacts";
const tag = process.env.RELEASE_TAG;
const repository = process.env.RELEASE_REPOSITORY;

if (!tag || !repository) {
  throw new Error("RELEASE_TAG and RELEASE_REPOSITORY are required");
}

const version = tag.replace(/^v/, "");
const baseUrl = `https://github.com/${repository}/releases/download/${tag}`;
const platforms = {
  "windows-x86_64": "Conversation-Reclaim-Windows-x64-Setup.exe",
  "darwin-aarch64": "Conversation-Reclaim-macOS-arm64.app.tar.gz",
  "darwin-x86_64": "Conversation-Reclaim-macOS-x64.app.tar.gz",
};

const metadata = {
  version,
  notes: `Conversation Reclaim ${tag}`,
  pub_date: new Date().toISOString(),
  platforms: Object.fromEntries(Object.entries(platforms).map(([platform, filename]) => [
    platform,
    {
      url: `${baseUrl}/${filename}`,
      signature: readFileSync(join(directory, `${filename}.sig`), "utf8").trim(),
    },
  ])),
};

writeFileSync(join(directory, "latest.json"), `${JSON.stringify(metadata, null, 2)}\n`);
