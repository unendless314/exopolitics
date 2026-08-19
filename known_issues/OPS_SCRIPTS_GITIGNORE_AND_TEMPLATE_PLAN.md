# Cloud Ops Scripts (.sh) Gitignore and Template Refactor Plan

- **Date**: 2026-08-19
- **Status**: Approved & Executed
- **Affected files**: `pipeline.sh`, `site-build.sh`, `.gitignore`, `pipeline.sh.example`, `site-build.sh.example`

---

## 1. Background & Problem Statement

In commit `15f6323` (*Implement publish export versioned generation and atomic pointer*), root automation scripts `pipeline.sh` and `site-build.sh` were committed to Git version control.

Upon alignment between local development and cloud server environments, two issues were identified:
1. **Environment Tight Coupling**: `pipeline.sh` and `site-build.sh` contain hardcoded cloud-only paths (`/root/.openclaw/workspace/exopolitics`, `/var/www/exopolitics`), systemd user units (`systemctl --user start site-build`), and Unix user ownership commands (`chown www-data`). These cannot run natively on Windows development environments.
2. **Information Disclosure & Privacy**: Committing hardcoded internal server paths into a public or shared Git repository violates the project's "Privacy by Default" principle and leaks cloud infrastructure topology.

---

## 2. Solution: Option A+ (Cloud-only Script Gitignore + Version-Controlled Templates)

To maintain a clean separation of concerns between OS-agnostic source code (Git repository) and cloud-specific ops deployment (Cloud Server), the following Option A+ design was agreed upon:

1. **Parameterised Templates (`.example`) in Git**:
   - Create `pipeline.sh.example` and `site-build.sh.example` in Git.
   - Replace all hardcoded server paths with clear placeholder variables (e.g. `WORKSPACE="${WORKSPACE:-/path/to/your/exopolitics}"`).
   - Preserve script structure, step sequence, `set -e -o pipefail`, and logging format.
2. **Remove Real Scripts from Git Tracking**:
   - Use `git rm --cached pipeline.sh site-build.sh` to untrack the real scripts while preserving local physical files on both local dev and cloud server.
3. **Re-add to `.gitignore`**:
   - Re-add `pipeline.sh` and `site-build.sh` to `.gitignore` to prevent future accidental commits.
4. **Post-Push Verification**:
   - Verify `git ls-tree origin/main` to ensure `pipeline.sh` / `site-build.sh` are untracked on remote while `.example` files and `.gitignore` rules are active.

---

## 3. Execution Specification & Verification Matrix

| Step | Action | Command / Target | Verification Criterion |
|---|---|---|---|
| 1 | Create `.example` templates | `pipeline.sh.example`, `site-build.sh.example` | Parameterized placeholders without hardcoded `/root` paths |
| 2 | Untrack real scripts | `git rm --cached pipeline.sh site-build.sh` | Local physical files preserved |
| 3 | Update `.gitignore` | Add `pipeline.sh` and `site-build.sh` | `git status` shows ignored |
| 4 | Commit & Push | `git commit` & `git push origin main` | Clean push to main |
| 5 | Verify Remote Tree | `git ls-tree origin/main` | No `.sh` blobs, only `.example` blobs and updated `.gitignore` |
