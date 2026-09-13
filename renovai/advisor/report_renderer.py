from .pre_purchase import AdvisoryReport, ChecklistItem


def render_report_md(report: AdvisoryReport) -> str:
    """Produces a human-readable Markdown document from an AdvisoryReport."""
    p = report.apartment_profile

    risk_emoji = {"alacsony": "🟢", "közepes": "🟡", "magas": "🔴"}.get(
        report.overall_risk, "❓"
    )

    md = f"# Felújítási Tanácsadói Jelentés\n\n"
    md += f"**Helyszín:** {p.address_district}. kerület | **Terület:** {p.floor_area_sqm} m² | **Kockázat:** {risk_emoji} {report.overall_risk.upper()}\n\n"

    md += "## Összefoglaló\n"
    md += f"{report.summary_hu}\n\n"

    md += "## Becsült felújítási költség\n"
    md += "| Kategória | Összeg |\n"
    md += "| :--- | ---: |\n"
    md += f"| **Alacsony becslés** | {report.cost_estimate['estimate_low_huf']:,} Ft |\n".replace(
        ",", " "
    )
    md += (
        f"| **Várható** | {report.cost_estimate['estimate_mid_huf']:,} Ft |\n".replace(
            ",", " "
        )
    )
    md += f"| **Magas becslés** | {report.cost_estimate['estimate_high_huf']:,} Ft |\n".replace(
        ",", " "
    )
    md += f"\n*Az árak {report.cost_estimate['inflation_adjusted_to']} időpontra inflációval korrigálva.*\n\n"

    def render_items(items: list[ChecklistItem], title: str):
        if not items:
            return ""

        res = f"## {title}\n"

        # Sort by priority
        for prio, emoji in [
            ("kritikus", "🔴"),
            ("fontos", "🟡"),
            ("érdemes_megnézni", "🔵"),
        ]:
            p_items = [it for it in items if it.priority == prio]
            if p_items:
                res += f"### {emoji} {prio.replace('_', ' ').capitalize()}\n"
                for it in p_items:
                    res += f"- **[{it.category}]** {it.item}\n"
                    res += f"  > *Miért fontos:* {it.why}\n"
                    if it.rag_source:
                        res += f"  > *Forrás:* {it.rag_source}\n"
                res += "\n"
        return res

    md += render_items(report.questions_for_seller, "Kérdések az eladónak")
    md += render_items(report.inspection_checklist, "Helyszíni ellenőrzési lista")
    md += render_items(report.red_flags, "Piros zászlók")

    if report.similar_cases:
        md += "## Hasonló esetek az adatbázisból\n"
        md += "| Forrás fájl | Kerület | Becsült összeg (mai áron) |\n"
        md += "| :--- | :---: | ---: |\n"
        for case in report.similar_cases:
            md += f"| {case['file']} | {case['address']} | {case['grand_total_adjusted']:,} Ft |\n".replace(
                ",", " "
            )
        md += "\n"

    md += "## Felhasznált szakmai források\n"
    for src in report.rag_sources_used:
        md += f"- {src}\n"

    return md
