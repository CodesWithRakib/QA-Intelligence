# fixture-vite (intentionally defective)

Static fixture for the QA platform's self-tests. Never deploy it.

| Seeded defect | Expected detector |
|---|---|
| `vite@5.0.0` has published advisories | dependency scan |
| `sum()` returns a wrong result, `src/math.test.js` fails | `test` pipeline step |
| Button without accessible name in `src/App.jsx` | forms / interactive check (when served) |
