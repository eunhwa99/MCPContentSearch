import os

from environments.runtime_env import load_repo_dotenv

load_repo_dotenv()

TISTORY_BLOG_NAME = os.getenv("TISTORY_BLOG_NAME", "")
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
