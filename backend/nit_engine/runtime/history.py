"""Local demonstration persistence. Authenticated IDs supplied by host only."""
import sqlite3
from pathlib import Path

class QuestionHistory:
    def __init__(self, path):
        self.path=str(path)
        Path(self.path).parent.mkdir(parents=True,exist_ok=True)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, course_id TEXT NOT NULL,
                student_id TEXT NOT NULL, question_text TEXT NOT NULL,
                phase TEXT NOT NULL CHECK(phase IN ('initial','revision')),
                parent_id INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    def _db(self):return sqlite3.connect(self.path)
    def record(self,course_id,student_id,question_text,phase,parent_id=None):
        if not course_id or not student_id or not question_text.strip() or len(question_text)>2000:
            raise ValueError('Invalid input')
        if phase not in ('initial','revision'):raise ValueError('Invalid phase')
        with self._db() as db:
            if phase=='revision':
                parent=db.execute('SELECT course_id, student_id FROM attempts WHERE id=?',(parent_id,)).fetchone()
                if not parent or parent!=(course_id,student_id):raise PermissionError('Revision parent not owned by student/course')
            elif parent_id is not None:raise ValueError('Initial question cannot have parent')
            cur=db.execute('INSERT INTO attempts(course_id,student_id,question_text,phase,parent_id) VALUES(?,?,?,?,?)',
                           (course_id,student_id,question_text,phase,parent_id))
            return cur.lastrowid
    def list_for_student(self,course_id,student_id):
        with self._db() as db:
            rows=db.execute('SELECT id,phase,question_text,parent_id,created_at FROM attempts WHERE course_id=? AND student_id=? ORDER BY id',
                            (course_id,student_id)).fetchall()
            return [{'id':r[0],'phase':r[1],'question':r[2],'parent_id':r[3],'created_at':r[4]} for r in rows]
