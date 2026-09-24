---
name: release
description: >-
  Automate AK5 version bump and release workflows for PyPI and npm.
  Use when releasing a new version of AK5 (patch, minor, or major),
  running pre-flight linting and test suites, compiling embedded Web UI assets,
  synchronizing lockfiles, and pushing release git tags for automated CI/CD deployment.
---

# AK5 Release Automation Skill

Use this skill when preparing and publishing a new release of **AK5** (Python package on PyPI, embedded Web UI, and TypeScript SDK).

---

## 1. Release Types & Semantic Versioning

Choose the appropriate release bump based on SemVer 2.0 (`MAJOR.MINOR.PATCH`):

| Release Type | Command | When to Use | Example |
|---|---|---|---|
| **`patch`** | `python3 scripts/release.py patch --push` | Bug fixes, documentation revisions, minor styling or security patches | `1.0.3` → `1.0.4` |
| **`minor`** | `python3 scripts/release.py minor --push` | New CLI commands, backwards-compatible API features, new MCP tools | `1.0.3` → `1.1.0` |
| **`major`** | `python3 scripts/release.py major --push` | Breaking API/schema changes, major architectural redesigns | `1.0.3` → `2.0.0` |

---

## 2. One-Command Automated Release

AK5 includes an automated release script that executes the complete quality and publishing pipeline:

```bash
# Preview version calculation without modifying files
python3 scripts/release.py patch --dry-run
python3 scripts/release.py minor --dry-run
python3 scripts/release.py major --dry-run

# Run full pipeline: test -> compile web UI -> bump versions -> lock -> build -> commit & push
python3 scripts/release.py patch --push
```

### What the automation script executes:
1. **Pre-flight Quality Check:** Runs `uv run ruff check backend` and `uv run pytest backend/tests`.
2. **Web UI Compilation:** Executes `scripts/build_web_ui.sh` to compile Next.js static assets into `backend/src/ak5/web_ui/`.
3. **Monorepo Version Sync:** Synchronizes version numbers in:
   - `backend/pyproject.toml`
   - `frontend/package.json`
   - `packages/sdk/package.json`
4. **Lockfile Synchronization:** Executes `uv lock` to update `uv.lock`.
5. **Distribution Validation:** Runs `uv build backend --out-dir dist` to verify wheel and sdist validity.
6. **Git Tag & Push:** Commits changes, tags `v<version>`, and pushes both `master` and the tag to GitHub `origin`.

---

## 3. Manual Step-by-Step Procedure

If you prefer to perform each step manually or need granular debugging:

### Step 1: Quality Checks
```bash
uv run ruff check backend
uv run pytest backend/tests
```

### Step 2: Build Embedded Static Web Assets
```bash
chmod +x scripts/build_web_ui.sh
./scripts/build_web_ui.sh
```

### Step 3: Bump Versions
Update the version string in the following files:
* `backend/pyproject.toml` (`version = "X.Y.Z"`)
* `frontend/package.json` (`"version": "X.Y.Z"`)
* `packages/sdk/package.json` (`"version": "X.Y.Z"`)

### Step 4: Update Lockfile & Test Wheel Build
```bash
uv lock
rm -rf dist
uv build backend --out-dir dist
unzip -l dist/*.whl | grep web_ui  # Verify web UI is bundled
```

### Step 5: Commit, Tag, and Push
```bash
VERSION="1.0.4"
git add backend/pyproject.toml frontend/package.json packages/sdk/package.json uv.lock
git commit -m "release: v${VERSION} — bump version to ${VERSION}"
git tag -a "v${VERSION}" -m "v${VERSION} release"
git push origin master
git push origin "v${VERSION}"
```

---

## 4. Post-Release Verification

After pushing the git tag, GitHub Actions automatically builds and publishes the package to PyPI:

### Step 1: Monitor GitHub Actions Run
```bash
gh run list --workflow=release.yml -L 1
```

### Step 2: Check PyPI Propagation
```bash
# Check latest registered version on PyPI
curl -s https://pypi.org/pypi/ak5/json | jq -r '.info.version'
```

### Step 3: Zero-Install Smoke Test
```bash
# Verify the published version executes via uvx
uvx --refresh ak5@latest --version

# Verify the embedded web dashboard launches
uvx ak5 web --help
```
