from __future__ import annotations

import argparse
import json


def main():
    parser = argparse.ArgumentParser(description="Player Graph: local, no-API pilot")
    parser.add_argument("command", choices=["discover", "collect", "refresh", "extract", "llm-extract", "build-graph", "graph-stats", "review-teacher", "export-sft", "discover-v2", "extend-vnexpress", "discover-v3", "collect-v2", "collect-v3", "collect-v4", "screen", "report", "import-neo4j"])
    parser.add_argument("--per-language", type=int, default=150)
    parser.add_argument("--reuse-candidates", action="store_true")
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--all", action="store_true", help="llm-extract over the whole corpus")
    parser.add_argument("--articles", default=None, help="article JSONL to use instead of the live corpus")
    parser.add_argument("--snapshot", default=None, help="screen: write kept articles here instead of in place")
    args = parser.parse_args()
    if args.command == "llm-extract":
        from .llm_extract import run
        output = run(args.run_name or "pilot20", args.model, args.per_language, args.workers, args.all, args.articles)
    elif args.command == "screen":
        from pathlib import Path
        from .screen import screen
        output = screen(Path(args.articles) if args.articles else None, Path(args.snapshot) if args.snapshot else None)
    elif args.command == "build-graph":
        from .resolve import build
        output = build(args.run_name)
    elif args.command in {"discover-v2", "extend-vnexpress", "discover-v3", "collect-v2", "collect-v3", "collect-v4"}:
        from .collect_v2 import collect_v2, discover_v2, discover_v3, extend_vnexpress
        from .common import DATA, read_jsonl
        from .fetch import Fetcher
        # Resumable batch: count what an earlier v3 run already added.
        n_v3 = lambda lang, c="v3": sum(1 for x in read_jsonl(DATA / "processed/articles.jsonl") if x.get("collection") == c and x["language"] == lang)
        command = {"discover-v2": discover_v2, "extend-vnexpress": extend_vnexpress, "discover-v3": discover_v3, "collect-v2": collect_v2,
                   # Third batch: about half the second, spread over many entities.
                   "collect-v3": lambda f: collect_v2(f, target_vi=max(0, 1000 - n_v3("en") - n_v3("vi")), target_en=0, max_per_topic=12, prefer=("topic3:", "sitemap3:"), collection="v3"),
                   # Fourth batch: tops the graph up past 5,000 edges from topic candidates not yet used.
                   "collect-v4": lambda f: collect_v2(f, target_vi=max(0, 2050 - n_v3("vi", "v4")), target_en=0, max_per_topic=20, prefer=("topic3:",), collection="v4")}[args.command]
        output = command(Fetcher())
    elif args.command in {"review-teacher", "export-sft"}:
        from .teacher_data import export_training, review_sample
        output = review_sample(args.run_name) if args.command == "review-teacher" else export_training(args.run_name, articles_path=args.articles)
    elif args.command == "graph-stats":
        from .stats import analyze
        output = analyze(args.run_name)
    elif args.command in {"discover", "collect", "refresh"}:
        from .fetch import Fetcher
        from .corpus import collect, discover, refresh_articles
        client = Fetcher()
        output = discover(client) if args.command == "discover" else (refresh_articles(client) if args.command == "refresh" else collect(client, args.per_language, not args.reuse_candidates))
    elif args.command == "extract":
        from .nlp import run_extraction
        output = run_extraction()
    elif args.command == "report":
        from .report import make_report
        output = make_report()
    else:
        from .common import DATA
        from .graph import import_neo4j
        output = import_neo4j(DATA / "graph" / args.run_name, args.run_name) if args.run_name else import_neo4j()
    if isinstance(output, dict):
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if args.command == "collect" and not output.get("complete", False):
            raise SystemExit(2)


if __name__ == "__main__":
    main()
