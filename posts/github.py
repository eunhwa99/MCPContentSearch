from environments.token import GITHUB_TOKEN
import httpx
from typing import List, Dict

async def fetch_github_files(username: str) -> List[Dict]:
    """GitHub에서 모든 코드 파일 내용 가져오기"""
    if not GITHUB_TOKEN:
        return []
    
    files = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 사용자의 저장소 목록 가져오기
            repos_response = await client.get(
                f"https://api.github.com/users/{username}/repos",
                headers={
                    "Authorization": f"token {GITHUB_TOKEN}",
                    "Accept": "application/vnd.github.v3+json",
                },
                params={"per_page": 100}
            )
            repos_response.raise_for_status()
            repos = repos_response.json()
            
            # 각 저장소에서 파일 검색 (최근 업데이트된 저장소 우선)
            for repo in repos[:20]:  # 최대 20개 저장소만
                repo_name = repo.get("name")
                
                # 저장소의 파일 트리 가져오기
                tree_response = await client.get(
                    f"https://api.github.com/repos/{username}/{repo_name}/git/trees/main?recursive=1",
                    headers={
                        "Authorization": f"token {GITHUB_TOKEN}",
                        "Accept": "application/vnd.github.v3+json",
                    }
                )
                
                if tree_response.status_code != 200:
                    continue
                
                tree_data = tree_response.json()
                tree = tree_data.get("tree", [])
                
                # 코드 파일만 필터링 (.py, .js, .cpp, .java 등)
               # code_extensions = [".py", ".js", ".ts", ".cpp", ".c", ".java", ".go", ".rs"]
                for item in tree:
                    if item.get("type") == "blob":
                        path = item.get("path", "")
                        #if any(path.endswith(ext) for ext in code_extensions):
                            # 파일 내용 가져오기
                        try:
                            content_response = await client.get(
                            f"https://api.github.com/repos/{username}/{repo_name}/contents/{path}",
                            headers={
                                        "Authorization": f"token {GITHUB_TOKEN}",
                                        "Accept": "application/vnd.github.v3.raw",
                                    }
                            )
                            if content_response.status_code == 200:
                                content = content_response.text
                                files.append({
                                        "id": f"github_{username}_{repo_name}_{path}",
                                        "platform": "GitHub",
                                        "title": f"{repo_name}/{path}",
                                        "content": content,
                                        "url": f"https://github.com/{username}/{repo_name}/blob/main/{path}",
                                        "date": repo.get("updated_at", "")
                                    })
                        except Exception as e:
                            print(f"Error fetching file {path}: {e}")
                            continue
    except Exception as e:
        print(f"GitHub fetch error: {e}")
    
    return files
