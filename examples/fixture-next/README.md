# fixture-next (intentionally defective)

Static fixture for the QA platform's self-tests. Never deploy it.

| Seeded defect | Expected detector |
|---|---|
| `next@14.1.0` has published advisories | dependency scan |
| `<img>` without alt text, input without label in `app/page.tsx` | accessibility (when served) |
| Type error in `app/dashboard/page.tsx` | `typecheck` pipeline step |
| No lockfile | pipeline note (non-reproducible install) |
