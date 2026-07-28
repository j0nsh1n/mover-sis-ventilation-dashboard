# Agent instructions (MOVER SIS Monitor)

Read and follow **`CONTEXT.md`** for durable project structure and process rules.

## Hard rules

1. **Version:** Always bump and keep **`x.y.z`** in sync (`VERSION`, `src/__version__.py`, release tags).
2. **Executable:** When shipping app changes, **rebuild and reinstall** with `./scripts/install_local.sh` (and rely on release workflow after merge to `main`).
3. **Context hygiene:** Update `CONTEXT.md` when architecture/behavior changes; **remove outdated non-structural narrative**. Keep structural and functional facts.
4. Prefer **PRs** for non-trivial work unless the user asks otherwise.
5. Research data only — never present outputs as clinical decision support.
