# Adding a harness

The native app discovers integrations through a small registry. A new harness
does not require changes to scanning totals, selection state, manifests, or the
cleanup executor.

## 1. Add one Rust module

Create `src-tauri/src/harnesses/my_tool.rs` and implement `Harness`:

```rust
use super::{Harness, HarnessMetadata};
use crate::engine::{add_tree_actions, Action};
use std::path::Path;

pub struct MyTool;

impl Harness for MyTool {
    fn metadata(&self) -> HarnessMetadata {
        HarnessMetadata {
            key: "my-tool",
            name: "My Tool",
            description_es: "Cachés y artefactos regenerables",
            description_en: "Caches and regenerable artifacts",
            logo: "my-tool",
            recommended: true,
            protected: true,
        }
    }

    fn allowed_roots(&self, home: &Path) -> Vec<std::path::PathBuf> {
        vec![home.join(".my-tool")]
    }

    fn plan(&self, home: &Path, actions: &mut Vec<Action>, _warnings: &mut Vec<String>) {
        add_tree_actions(
            actions,
            &home.join(".my-tool/cache"),
            "my-tool",
            "regenerable My Tool cache",
        );
    }
}
```

Use `add_trim_actions` for strict JSONL compaction markers. Write a custom
planner for SQLite or other formats, while still producing `Action` records.

## 2. Register it

Declare the module and add `Box::new(my_tool::MyTool)` in
`src-tauri/src/harnesses/mod.rs`. This is the only central list.

`allowed_roots` is mandatory: the executor rejects every planned path outside
those roots. Keep roots as narrow as possible.

## 3. Add an optional logo

Add a component to the `logos` map in `src/BrandLogos.tsx`. If no logo exists,
the UI automatically uses the neutral harness symbol. Cards and translations
come from `HarnessMetadata`; no edit to `App.tsx` is necessary.

## 4. Test the format

Add fixtures that prove:

- real structural markers are accepted;
- quoted or nested marker text is rejected;
- malformed data fails closed;
- active files and symbolic links are preserved;
- every action stays within `allowed_roots`.

Then run:

```bash
npm run build
cargo test --manifest-path src-tauri/Cargo.toml
python3 -m unittest discover -v
```

The Python CLI is intentionally independent. If the same integration needs
advanced CLI/database cleanup, add `scan_<tool>` and `apply_<tool>` adapters to
`reclaim.py` with the same marker and safety tests.
