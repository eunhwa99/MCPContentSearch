import os
from dotenv import load_dotenv
load_dotenv()

TISTORY_BLOG_NAME = os.getenv("TISTORY_BLOG_NAME", "")
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
