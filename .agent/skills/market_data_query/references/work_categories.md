# Work Categories — RenovAI Market Data Query Skill

The market database uses the following work category keys for filtering
and aggregation.  These map to the `work_categories` table in the SQLite
database.

| Key               | Label (HU)              | Label (EN)           | Cost type       |
|-------------------|-------------------------|----------------------|-----------------|
| villany          | Villanyszerelés         | Electrical work      | labor+materials |
| viz_futes        | Víz- és fűtésszerelés   | Plumbing & heating   | labor+materials |
| burkolas         | Burkolás                | Flooring             | materials-heavy |
| bontas           | Bontás                  | Demolition           | labor-heavy     |
| festes           | Festés / mázolás        | Painting             | labor-heavy     |
| nyilaszaro       | Nyílászáró csere        | Windows/doors        | materials-heavy |
| konyha           | Konyhabútor / konyha    | Kitchen              | materials-heavy |
| furdo            | Fürdőszoba              | Bathroom             | labor+materials |
| futes_rendszer   | Fűtésrendszer           | Heating system       | materials-heavy |
| szigeteles       | Szigetelés              | Insulation           | materials-heavy |
| egyeb            | Egyéb                   | Other                | varies          |

## Example queries the Text-to-SQL engine can handle
- "Mennyibe került átlagosan a villanyszerelés az elmúlt 3 évben?"
- "Melyik kerületben voltak a legdrágább felújítások?"
- "Hány darab burkolási tétel van a teljes adatbázisban?"
- "Mekkora a legkisebb és legnagyobb lakásméret a mintában?"
