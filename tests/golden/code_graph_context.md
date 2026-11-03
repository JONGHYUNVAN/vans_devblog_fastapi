# Code Graph Context: ts_app (`aaaaaaa`)

- Analyzed At: 2026-11-03T12:00:00+00:00
- Files: 14 | Internal Edges: 15 | Symbols: 16
- Tech Stack: next ^16.0.0, react ^19.0.0

## 1. Layers & Allowed Imports
| Layer | Files | May Import |
|---|---|---|
| app | 5 | components, hooks, lib, store, constants |
| components | 3 | hooks, lib, store, constants |
| hooks | 1 | lib, store, constants |
| store | 1 | lib, constants |
| lib | 3 | constants |
| constants | 1 | - |

## 2. Layer Violations (Do Not Add More)
- Violations (2):
  - `src/components/Button.tsx` (components) -> `src/app/shell.tsx` (app)
  - `src/lib/api.ts` (lib) -> `src/store/index.ts` (store)
- Declared Exceptions (1):
  - `src/constants/site.ts` -> `src/lib/format.service.ts`

## 3. Cycles & Orphans
- Cycles (1): `src/lib/api.ts -> src/store/index.ts -> src/lib/api.ts`
- Orphans (1): `src/lib/orphan.ts`

## 4. Hub Files (Top In-Degree)
| File | Layer | In | Out | Key Symbols |
|---|---|---|---|---|
| `src/lib/api.ts` | lib | 5 | 2 | Post, Id, Kind, Client, get |
