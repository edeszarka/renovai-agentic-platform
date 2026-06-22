import time
import logging
from datetime import datetime
from typing import List
from rich.console import Console
from rich.table import Table

from ..rag.pipeline import RAGPipeline
from .models import EvalExample, EvalResult, EvalSuiteReport
from .dataset import load_golden_dataset

logger = logging.getLogger(__name__)
console = Console()

class EvalRunner:
    def __init__(self, pipeline: RAGPipeline):
        self.pipeline = pipeline

    def run_example(self, example: EvalExample) -> EvalResult:
        start_time = time.time()
        
        # 1. Run RAG query
        try:
            response = self.pipeline.query(example.question)
            answer = response.answer.lower()
            sources = response.sources_cited
        except Exception as e:
            logger.error(f"Error evaluating example {example.id}: {e}")
            return EvalResult(
                example_id=example.id,
                question=example.question,
                actual_answer=f"ERROR: {e}",
                actual_sources=[],
                keyword_match_score=0.0,
                source_recall=0.0,
                model_used="error",
                duration_sec=time.time() - start_time
            )

        # 2. Calculate Keyword Match
        matches = [kw for kw in example.expected_answer_keywords if kw.lower() in answer]
        kw_score = len(matches) / len(example.expected_answer_keywords) if example.expected_answer_keywords else 1.0

        # 3. Calculate Source Recall
        # Check if expected files are in actual cited sources
        found_sources = [s for s in example.expected_sources if any(s in actual for actual in sources)]
        source_recall = len(found_sources) / len(example.expected_sources) if example.expected_sources else 1.0
        
        return EvalResult(
            example_id=example.id,
            question=example.question,
            actual_answer=response.answer,
            actual_sources=sources,
            keyword_match_score=kw_score,
            source_recall=source_recall,
            model_used=getattr(response, 'model_used', 'unknown'),
            duration_sec=time.time() - start_time
        )

    def run_suite(self, dataset: List[EvalExample]) -> EvalSuiteReport:
        results = []
        for ex in dataset:
            console.print(f"Testing [cyan]{ex.id}[/cyan]: {ex.question[:50]}...")
            res = self.run_example(ex)
            results.append(res)
            
        avg_kw = sum(r.keyword_match_score for r in results) / len(results)
        avg_src = sum(r.source_recall for r in results) / len(results)
        
        return EvalSuiteReport(
            timestamp=datetime.now().isoformat(),
            total_examples=len(results),
            avg_keyword_score=avg_kw,
            avg_source_recall=avg_src,
            results=results
        )

def display_report(report: EvalSuiteReport):
    table = Table(title=f"RAG Evaluation Report - {report.timestamp}")
    table.add_column("ID", style="dim")
    table.add_column("Category")
    table.add_column("Keywords %", justify="right")
    table.add_column("Recall %", justify="right")
    table.add_column("Time (s)", justify="right")

    for res in report.results:
        # We need to map back to category from dataset if possible, 
        # but let's keep it simple for now.
        table.add_row(
            res.example_id,
            "N/A",
            f"{res.keyword_match_score*100:.1f}%",
            f"{res.source_recall*100:.1f}%",
            f"{res.duration_sec:.2f}"
        )
        
    console.print(table)
    console.print(f"\n[bold]Aggregated Stats:[/bold]")
    console.print(f"Avg Keyword Match: [green]{report.avg_keyword_score*100:.1f}%[/green]")
    console.print(f"Avg Source Recall: [green]{report.avg_source_recall*100:.1f}%[/green]")
