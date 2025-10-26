import os
from dotenv import load_dotenv
load_dotenv()

TISTORY_ACCESS_TOKEN = os.getenv("TISTORY_ACCESS_TOKEN", "")
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "") 
