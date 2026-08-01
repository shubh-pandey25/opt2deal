import sys
import os
import argparse
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from config import get_groq_client, DEFAULT_MODEL
from orchestrator import InventoryOrchestrator

console = Console()

def main():
    parser = argparse.ArgumentParser(
        description="Multi-Agent Inventory Application Finder CLI (Groq Edition)"
    )
    parser.add_argument(
        "input",
        type=str,
        help="Description of the electrical component (e.g., 'Samsung LPDDR5 16GB')"
    )
    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help=f"Groq Chat model to use (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="report.md",
        help="Path to save the generated markdown report (default: report.md)"
    )
    parser.add_argument(
        "--refinements",
        type=int,
        default=1,
        help="Max refinement loops on QA rejection (default: 1)"
    )
    
    args = parser.parse_args()
    
    console.print(Panel.fit(
        "[bold green]Multi-Agent Electrical Component Application Finder[/bold green]",
        subtitle="Groq Inference Engine"
    ))
    
    # 1. Initialize Client
    try:
        client = get_groq_client()
    except ValueError as e:
        console.print(f"[bold red]Configuration Error:[/bold red] {e}")
        console.print("\nPlease create a [yellow].env[/yellow] file in the current directory and add:")
        console.print("[green]GROQ_API_KEY=your_groq_api_key_here[/green]\n")
        sys.exit(1)

    # 2. Run orchestrator
    orchestrator = InventoryOrchestrator(client=client, model=args.model)
    
    console.print(f"[bold blue]Processing Component:[/bold blue] [yellow]{args.input}[/yellow]")
    console.print(f"[bold blue]Using Model:[/bold blue] {args.model}\n")
    
    def log_cb(msg: str):
        # Format the log line based on agent name
        if "[SpecsExtractor]" in msg:
            console.print(f"[cyan]{msg}[/cyan]")
        elif "[AppSpecialist]" in msg:
            console.print(f"[magenta]{msg}[/magenta]")
        elif "[Synthesis]" in msg:
            console.print(f"[green]{msg}[/green]")
        elif "[QualityAssurance]" in msg:
            console.print(f"[yellow]{msg}[/yellow]")
        elif "[Orchestrator]" in msg:
            console.print(f"[blue]{msg}[/blue]")
        else:
            console.print(msg)

    try:
        result = orchestrator.run_pipeline(
            user_input=args.input,
            log_callback=log_cb,
            max_refinement_loops=args.refinements
        )
    except Exception as e:
        console.print(f"\n[bold red]Execution failed:[/bold red] {e}")
        sys.exit(1)

    # 3. Output results
    report = result["report"]
    output_path = os.path.abspath(args.output)
    
    # Write report file
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report)
        console.print(f"\n[bold green][SUCCESS] Report successfully saved to:[/bold green] [underline]{output_path}[/underline]\n")
    except Exception as e:
        console.print(f"\n[bold red]Failed to save report to {output_path}:[/bold red] {e}")
        
    console.print(Panel(
        Markdown(report),
        title="[bold green]Final Synthesized Inventory Report[/bold green]",
        expand=False
    ))

if __name__ == "__main__":
    main()
