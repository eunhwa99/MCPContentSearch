import hashlib

class ContentHasher:
    """컨텐츠 해시 유틸리티"""
    
    @staticmethod
    def hash_content(content: str) -> str:
        """MD5 해시 생성"""
        return hashlib.md5(content.encode("utf-8")).hexdigest()