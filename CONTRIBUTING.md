# Contributing to slimgest

Thanks for your interest in contributing to **slimgest**. slimgest is a
[Proximile LLC](https://proximile.llc) fork of
[gitingest](https://github.com/coderamp-labs/gitingest); we aim to keep
the contribution path friendly to first-time contributors.

---

## Non-technical ways to contribute

- **File an issue** for bugs or feature ideas:
  [open an issue](https://github.com/proximile/slimgest/issues/new) on
  this repository.
- **Use slimgest** on your own repos and report what worked or didn't —
  real-world feedback is the most valuable kind.
- If the bug exists in upstream gitingest too, consider also filing it
  upstream at
  [coderamp-labs/gitingest](https://github.com/coderamp-labs/gitingest)
  so all downstream forks benefit.

---

## Submitting a pull request

> **Prerequisites:** Python 3.9+ and `pre-commit` for the linter hooks.

1. **Fork** this repository and **clone** your fork:

   ```bash
   git clone https://github.com/<your-handle>/slimgest.git
   cd slimgest
   ```

2. **Set up the dev environment**:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev,server]"
   pre-commit install
   ```

3. **Create a branch** for your changes:

   ```bash
   git checkout -b your-branch
   ```

4. **Make your changes** and add tests where relevant.

5. **Run the test suite**:

   ```bash
   pytest
   ```

6. *(Optional)* **Run `pre-commit` on all files** to check hooks without
   committing:

   ```bash
   pre-commit run --all-files
   ```

7. **Commit** with a clear message:

   ```bash
   git commit -m "feat: short description"
   ```

8. **Push** your branch and open a pull request against `main` on
   `proximile/slimgest`:

   ```bash
   git push origin your-branch
   ```

   Pull request titles should follow the
   [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/)
   specification — this keeps the changelog readable.

9. **Iterate** on review feedback.

## Pulling in upstream changes

slimgest tracks upstream gitingest. When upstream lands a change that
also belongs here, the maintainers will rebase or cherry-pick it onto
slimgest's `main`. Contributors don't need to do this manually — just
make sure your PR description notes if it's a port of an upstream change
so we don't accidentally duplicate it.
