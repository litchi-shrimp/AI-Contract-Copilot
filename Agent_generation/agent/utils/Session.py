import time

class Session:
    def __init__(self, session_id=0, timeout_seconds=1800):  # 30分钟
        self.session_id = session_id
        self.last_active = time.time()
        self.timeout = timeout_seconds

    def is_expired(self):
        return time.time() - self.last_active > self.timeout
    
    def refresh(self):
        self.last_active = time.time()