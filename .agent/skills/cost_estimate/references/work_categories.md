# Work Categories — RenovAI Cost Estimate Skill

Lookup table mapping Hungarian scope keywords to renovation work categories
and typical cost drivers.

| Hungarian keyword | English         | Cost type       | Typical % of total |
|------------------|-----------------|-----------------|-------------------|
| villany          | Electrical      | labor+materials | 8–15%             |
| viz_futes        | Plumbing/heating| labor+materials | 10–20%            |
| burkolas         | Flooring        | materials-heavy | 5–12%             |
| bontas           | Demolition      | labor-heavy     | 3–8%              |
| festes           | Painting        | labor-heavy     | 3–6%              |
| nyilaszaro       | Windows/doors   | materials-heavy | 10–18%            |
| konyha           | Kitchen         | materials-heavy | 10–20%            |
| furdo            | Bathroom        | labor+materials | 8–15%             |
| futes_rendszer   | Heating system  | materials-heavy | 5–12%             |
| szigeteles       | Insulation      | materials-heavy | 3–8%              |
| teljes           | Full renovation | all             | 100%              |

## Notes
- Percentages are derived from the n=30 historical quote corpus and are
  indicative, not guaranteed.
- Slag (kohósalak) removal is a separate cost driver not captured in scope
  keywords — flag it via `known_issues` in the due-diligence skill.
- Shared plumbing stacks (osztott közmű) may require co-owner approval and
  add cost; ask via the due-diligence checklist.
