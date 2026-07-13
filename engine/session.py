"""Per-user sessions with evidence tracking.

Confidence in Rivet is earned, not asserted. Every file a chip actually
reads during a plan is recorded here; the discipline gate grades the
final answer against this record:

  HIGH    the answer's claims are backed by source files read this session
  MEDIUM  backed by architecture map / audit / pack knowledge only
  LOW     reasoning from architecture with no matching source at all

Sessions are isolated per user (faculty-wide deployment requirement —
no context bleed between users).
"""

import time
from dataclasses import dataclass, field


@dataclass
class Session:
    user_id: str
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    history: list = field(default_factory=list)          # [{role, content}]
    files_read: set = field(default_factory=set)          # repo-relative paths
    evidence: list = field(default_factory=list)          # [{kind, ref, chip}]
    request_count: int = 0

    def touch(self) -> None:
        self.last_active = time.time()

    def record_file_read(self, path: str, chip: str) -> None:
        self.files_read.add(path)
        self.evidence.append({"kind": "source_file", "ref": path, "chip": chip})

    def record_evidence(self, kind: str, ref: str, chip: str) -> None:
        self.evidence.append({"kind": kind, "ref": ref, "chip": chip})

    def add_turn(self, role: str, content: str, max_turns: int = 20) -> None:
        self.history.append({"role": role, "content": content})
        if len(self.history) > max_turns:
            self.history = self.history[-max_turns:]


class SessionManager:
    def __init__(self, ttl_seconds: int = 3600, rate_limit_per_hour: int = 60):
        self.ttl = ttl_seconds
        self.rate_limit = rate_limit_per_hour
        self._sessions: dict[str, Session] = {}
        self._request_log: dict[str, list] = {}

    def get(self, user_id: str) -> Session:
        self._expire()
        session = self._sessions.get(user_id)
        if session is None:
            session = Session(user_id=user_id)
            self._sessions[user_id] = session
        session.touch()
        return session

    def check_rate_limit(self, user_id: str) -> bool:
        """True if the user is within their hourly budget."""
        now = time.time()
        log = [t for t in self._request_log.get(user_id, []) if now - t < 3600]
        self._request_log[user_id] = log
        if len(log) >= self.rate_limit:
            return False
        log.append(now)
        return True

    def _expire(self) -> None:
        now = time.time()
        stale = [uid for uid, s in self._sessions.items()
                 if now - s.last_active > self.ttl]
        for uid in stale:
            del self._sessions[uid]

    def stats(self) -> dict:
        return {
            "active_sessions": len(self._sessions),
            "users": sorted(self._sessions.keys()),
        }
