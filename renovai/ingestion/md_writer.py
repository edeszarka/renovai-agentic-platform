from pathlib import Path
from .models import RenovationQuote

def format_huf(val) -> str:
    if val is None:
        return "0"
    return f"{int(val):,}".replace(",", " ")

def write_markdown(quote: RenovationQuote, output_path: Path) -> None:
    """Produces the Markdown RAG chunk file."""
    meta = quote.metadata
    
    lines = []
    lines.append("---")
    lines.append(f"source: {meta.file_name}")
    lines.append(f"address: {meta.address_raw or 'Ismeretlen'}")
    lines.append(f"district: {meta.district or 'Nincs'}")
    lines.append(f"postal_code: {meta.postal_code or 'Nincs'}")
    lines.append(f"quote_style: {quote.quote_style}")
    lines.append(f"quote_date: {meta.quote_date or 'Ismeretlen'}")
    lines.append(f"grand_total_huf: {meta.grand_total}")
    lines.append(f"timeline: {meta.timeline_weeks_min or '?'}-{meta.timeline_weeks_max or '?'} hét")
    lines.append(f"start_date: {meta.start_date_approx or 'Nincs megadva'}")
    lines.append(f"has_slag: {'true' if meta.has_slag_complication else 'false'}")
    lines.append("---")
    lines.append("")
    lines.append(f"# Árajánlat — {meta.address_raw or meta.file_name}")
    lines.append("")
    
    # Organize by phase
    phases = sorted(list(set(li.phase for li in quote.line_items + quote.alternatives)))
    
    for phase in phases:
        if len(phases) > 1:
            lines.append(f"## {phase}. fázis")
            lines.append("")

        # Main scope for this phase
        main_items = [li for li in quote.line_items if li.phase == phase]
        if main_items:
            lines.append("### Főbb munkák")
            lines.append("| Munka | Munkadíj (Ft) | Anyag (Ft) | Összesen (Ft) |")
            lines.append("|:------|--------------:|-----------:|--------------:|")
            for item in main_items:
                lines.append(f"| {item.name} | {format_huf(item.labor_cost)} | {format_huf(item.material_cost)} | {format_huf(item.total_cost)} |")
            lines.append("")

        # Alternatives for this phase
        alt_items = [li for li in quote.alternatives if li.phase == phase]
        if alt_items:
            lines.append("### Alternatív / opcionális munkák")
            lines.append("| Munka | Munkadíj (Ft) | Anyag (Ft) | Összesen (Ft) |")
            lines.append("|:------|--------------:|-----------:|--------------:|")
            for item in alt_items:
                lines.append(f"| {item.name} (v{item.version}) | {format_huf(item.labor_cost)} | {format_huf(item.material_cost)} | {format_huf(item.total_cost)} |")
            lines.append("")

    # Detailed notes for items that have them
    all_items_with_notes = [li for li in quote.line_items + quote.alternatives if li.notes]
    if all_items_with_notes:
        lines.append("## Részletes megjegyzések")
        for item in all_items_with_notes:
            lines.append(f"### {item.name}")
            lines.append(item.notes)
            lines.append("")

    if quote.not_included:
        lines.append("## Ami nincs benne az árban")
        for item in quote.not_included:
            lines.append(f"- {item}")
        lines.append("")

    if quote.buyer_purchases:
        lines.append("## Tulajdonos által vásárolt tételek")
        for item in quote.buyer_purchases:
            lines.append(f"- {item}")
        lines.append("")

    if quote.general_notes:
        lines.append("## Általános megjegyzések")
        for note in quote.general_notes:
            lines.append(f"- {note}")
        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
