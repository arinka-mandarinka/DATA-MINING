# Список репозиториев пользователя

import json
import urllib.parse
import urllib.request

# Настройки
USERNAME = "arinka-mandarinka"
PER_PAGE = 100
SORT = "updated"
OUTPUT = "github_repos.json"

HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "Python-GitHub-API/1.0",
}


def fetch_repos(username):
    repos = []
    page = 1

    while True:
        query = urllib.parse.urlencode({
            "per_page": PER_PAGE,
            "page": page,
            "sort": SORT,
            "direction": "desc",
        })
        url = f"https://api.github.com/users/{username}/repos?{query}"

        request = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=15) as response:
            batch = json.loads(response.read())

        repos.extend(batch)

        if len(batch) < PER_PAGE:
            break
        page += 1

    return repos


def extract(repo):
    return {
        "id": repo["id"],
        "name": repo["name"],
        "full_name": repo["full_name"],
        "description": repo["description"],
        "language": repo["language"],
        "fork": repo["fork"],
        "stars": repo["stargazers_count"],
        "forks": repo["forks_count"],
        "open_issues": repo["open_issues_count"],
        "url": repo["html_url"],
        "clone_url": repo["clone_url"],
        "created_at": repo["created_at"],
        "updated_at": repo["updated_at"],
        "topics": repo.get("topics", []),
    }


def main():
    raw = fetch_repos(USERNAME)

    result = {
        "username": USERNAME,
        "total_count": len(raw),
        "sorted_by": SORT,
        "repositories": [extract(r) for r in raw],
    }

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()