# Construction Planner — Sequencing & Cost Reference

## Mandatory Phase Order

| Phase | Name | Dependencies | Duration Estimate |
|-------|------|--------------|-------------------|
| 0 | Salak bontás | Statikus szakvélemény előtte | 2-3 days |
| 1 | Bontás | Phase 0 complete | 3-7 days |
| 2 | Kőműves | Phase 1 complete | 5-10 days |
| 3 | Gépészet | Phase 2 complete | 7-14 days |
| 4 | Vakolás | Phase 3 complete, walls dry | 5-10 days |
| 5 | Burkolás | Phase 4 dry (min 3 days) | 7-14 days |
| 6 | Festés | Phase 5 complete | 5-7 days |

## Cost Ranges by Phase (per 55 nm reference apartment)

| Phase | Material Low | Material High | Labor Low | Labor High |
|-------|-------------|---------------|-----------|------------|
| Salak bontás (Phase 0) | 0 Ft | 0 Ft | 165 000 Ft | 275 000 Ft |
| Bontás (Phase 1) | 0 Ft | 50 000 Ft | 55 000 Ft | 110 000 Ft |
| Kőműves (Phase 2) | 249 000 Ft | 402 000 Ft | 280 000 Ft | 420 000 Ft |
| Gépészet (Phase 3) | 200 000 Ft | 500 000 Ft | 500 000 Ft | 1 000 000 Ft |
| Vakolás (Phase 4) | 80 000 Ft | 180 000 Ft | 180 000 Ft | 380 000 Ft |
| Burkolás (Phase 5) | 130 000 Ft | 250 000 Ft | 300 000 Ft | 600 000 Ft |
| Festés (Phase 6) | 50 000 Ft | 100 000 Ft | 200 000 Ft | 400 000 Ft |

## Area Scaling Formula

```
scaled_cost = reference_cost * (user_area_sqm / 55)
```

## Surcharge Rules

| Condition | Phase | Surcharge |
|-----------|-------|-----------|
| Fűrészporos tapéta | Phase 4 | +100 000 Ft material, +180 000-280 000 Ft labor |
| Épített zuhany | Phase 5 | +130 000 Ft material (cement waterproofing), +50 000-80 000 Ft labor |
| Kohósalak | Phase 0 | +3 000-5 000 Ft/nm labor |
