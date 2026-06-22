import re
from pathlib import Path
from typing import List, Tuple, Dict, Any

import tiktoken
from pydantic import BaseModel

# --- Constants ---

ENCODING = tiktoken.get_encoding("cl100k_base")
DEFAULT_MAX_TOKENS = 512
DEFAULT_OVERLAP_TOKENS = 50

# --- Models ---

class Chunk(BaseModel):
    """Represents a piece of text ready for embedding with associated metadata."""
    chunk_id: str           # "<source_stem>_<section>_<idx>"
    source_file: str
    source_type: str = "quote"
    section: str            # e.g. "Főbb munkák", "Részletes megjegyzések"
    content: str            # the actual text to embed
    metadata: dict          # all YAML front-matter fields + section name

# --- Utilities ---

def count_tokens(text: str) -> int:
    """Returns the token count of a given string using cl100k_base."""
    return len(ENCODING.encode(text))

def format_huf(val: int) -> str:
    """Formats integer as Hungarian currency string: 10558502 -> '10 558 502'."""
    return f"{val:,}".replace(",", " ")

def sanitize_id(text: str) -> str:
    """Creates a URL/File-safe string for use in IDs."""
    return re.sub(r'[^a-zA-Z0-9]', '_', text).strip('_')

def parse_front_matter(content: str) -> Tuple[Dict[str, Any], str]:
    """
    Surgically extracts YAML front matter from markdown content.
    Returns (metadata_dict, body_text).
    """
    if not content.startswith("---"):
        return {}, content
        
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
        
    yaml_text = parts[1]
    body = parts[2].strip()
    
    metadata = {}
    for line in yaml_text.strip().split("\n"):
        if ":" not in line:
            continue
            
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        
        # Handle simple lists: [item1, item2]
        if value.startswith("[") and value.endswith("]"):
            metadata[key] = [item.strip() for item in value[1:-1].split(",") if item.strip()]
        else:
            # Type casting for numbers
            try:
                metadata[key] = float(value) if "." in value else int(value)
            except ValueError:
                metadata[key] = value
                
    return metadata, body

# --- Splitting Logic ---

def _split_by_headings(content: str, level: int = 2) -> List[Tuple[str, str]]:
    """Splits markdown by headings of a specific level (e.g. '## ' for level 2)."""
    prefix = "#" * level + " "
    lines = content.split("\n")
    
    sections = []
    current_title = "Intro"
    current_lines = []
    
    for line in lines:
        if line.startswith(prefix):
            if current_lines:
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = line[len(prefix):].strip()
            current_lines = []
        elif level == 2 and line.startswith("# "):
            continue # Skip H1
        else:
            current_lines.append(line)
            
    if current_lines:
        sections.append((current_title, "\n".join(current_lines).strip()))
        
    return [(t, c) for t, c in sections if c.strip()]

def _split_into_paragraphs(text: str) -> List[str]:
    """Splits text into paragraphs by double newlines."""
    return [p.strip() for p in text.split("\n\n") if p.strip()]

def _split_into_sentences(text: str) -> List[str]:
    """Splits a long paragraph into sentences."""
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]

def split_section(
    section_name: str, 
    content: str, 
    max_tokens: int, 
    overlap_tokens: int
) -> List[Tuple[str, str]]:
    """
    Recursively splits a section content until it fits within max_tokens.
    Order: Headings (H3) -> Paragraphs -> Sentences.
    """
    if count_tokens(content) <= max_tokens:
        return [(section_name, content)]
        
    # 1. Try splitting by H3
    subsections = _split_by_headings(content, level=3)
    if len(subsections) > 1 or (subsections and subsections[0][0] != "Intro"):
        results = []
        for sub_title, sub_content in subsections:
            full_title = f"{section_name} - {sub_title}" if sub_title != "Intro" else section_name
            results.extend(split_section(full_title, sub_content, max_tokens, overlap_tokens))
        return results
        
    # 2. Try splitting by Paragraphs + Sliding Window
    paragraphs = _split_into_paragraphs(content)
    para_blocks = []
    for p in paragraphs:
        if count_tokens(p) > max_tokens:
            # Paragraph is too long, split it further by sentences
            para_blocks.extend(_split_into_sentences(p))
        else:
            para_blocks.append(p)
            
    # 3. Combine into chunks with overlap
    chunks = []
    current_chunk = []
    current_tokens = 0
    
    for block in para_blocks:
        block_tokens = count_tokens(block)
        
        # If a single block exceeds limit, we must split it by characters (fallback)
        # but usually sentences fit. If not, we just take it as is for now.
        
        if current_tokens + block_tokens > max_tokens and current_chunk:
            chunks.append("\n\n".join(current_chunk))
            # Create overlap
            overlap_block = current_chunk[-1] if len(current_chunk) > 1 else ""
            if overlap_block and count_tokens(overlap_block) < overlap_tokens:
                current_chunk = [overlap_block, block]
            else:
                current_chunk = [block]
            current_tokens = sum(count_tokens(b) for b in current_chunk)
        else:
            current_chunk.append(block)
            current_tokens += block_tokens
            
    if current_chunk:
        chunks.append("\n\n".join(current_chunk))
        
    return [(f"{section_name} - chunk {i+1}", text) for i, text in enumerate(chunks)]

# --- Main Ingestion Logic ---

def chunk_markdown_file(
    md_path: Path,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS
) -> List[Chunk]:
    """Chunks a single markdown file using section-aware H2 splitting."""
    content = md_path.read_text(encoding="utf-8")
    metadata, body = parse_front_matter(content)
    
    source_type: str = "quote"
    
    # Construct Context Header for each chunk
    addr = metadata.get("address", "Ismeretlen cím")
    total = metadata.get("grand_total_huf", metadata.get("grand_total", 0))
    header = f"Helyszín: {addr} | Összesen: {format_huf(total) if isinstance(total, int) else total} Ft\n\n"
        
    # Adjust max tokens for content
    content_max = max_tokens - count_tokens(header)
    
    # Split by H2
    sections = _split_by_headings(body, level=2)
    chunks = []
    
    for sec_title, sec_content in sections:
        blocks = split_section(sec_title, sec_content, content_max, overlap_tokens)
        for sub_title, sub_content in blocks:
            chunk_metadata = {
                **metadata,
                "section": sub_title,
                "source_type": source_type,
                "source_file": md_path.name
            }
            
            chunk_id = f"{md_path.stem}_{sanitize_id(sub_title)}"
            
            chunks.append(Chunk(
                chunk_id=chunk_id,
                source_file=md_path.name,
                source_type=source_type,
                section=sub_title,
                content=header + sub_content,
                metadata=chunk_metadata
            ))
            
    return chunks

def chunk_all(
    quotes_md_dir: Path,
) -> List[Chunk]:
    """Finds and chunks all quote markdown files in the provided directory."""
    all_chunks = []
    
    if quotes_md_dir.exists():
        for file in quotes_md_dir.glob("*.md"):
            all_chunks.extend(chunk_markdown_file(file))
            
    return all_chunks
