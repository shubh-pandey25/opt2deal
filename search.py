import os
import json
import urllib.request
import urllib.parse
import warnings
# Silence the duckduckgo_search renaming warning to keep server logs clean
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*duckduckgo_search.*")
from duckduckgo_search import DDGS

def search_part_number(query: str) -> str:
    print(f"[+] Querying web for: {query}...")
    serper_api_key = os.getenv("SERPER_API_KEY")
    if serper_api_key:
        try:
            url = "https://google.serper.dev/search"
            headers = {
                "X-API-KEY": serper_api_key,
                "Content-Type": "application/json"
            }
            data = json.dumps({"q": query}).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10) as res:
                res_data = json.loads(res.read().decode("utf-8"))
                organic = res_data.get("organic", [])
                if organic:
                    lines = []
                    for i, item in enumerate(organic[:10]):
                        lines.append(
                            f"Result {i+1}:\n"
                            f"Title: {item.get('title', 'N/A')}\n"
                            f"URL: {item.get('link', 'N/A')}\n"
                            f"Snippet: {item.get('snippet', 'N/A')}\n"
                        )
                    return "\n".join(lines)
        except Exception as e:
            print(f"[-] Serper search failed, falling back to DDG: {e}")
            
    # Fallback to DuckDuckGo search using duckduckgo-search package
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=10))
            if results:
                lines = []
                for i, item in enumerate(results):
                    lines.append(
                        f"Result {i+1}:\n"
                        f"Title: {item.get('title', 'N/A')}\n"
                        f"URL: {item.get('href', 'N/A')}\n"
                        f"Snippet: {item.get('body', 'N/A')}\n"
                    )
                return "\n".join(lines)
    except Exception as e:
        print(f"[-] DuckDuckGo search failed: {e}")

    return "No direct search results found."
