"""Server-side authorization gate: deny by default; not a regulatory classifier."""
from typing import Literal
VALID={'PROHIBITED','RESTRICTED_HINT_ONLY','PERMITTED'}
def authorize(policy: str, action: str, role: str='student', teacher_approved: bool=False):
    if policy not in VALID: return False
    if role not in ('student','teacher'): return False
    if action=='native_search':return True
    if action in ('teacher_review','teacher_notes'):
        return role=='teacher'
    if action=='question_coach':return policy!='PROHIBITED' and (policy=='PERMITTED' or teacher_approved)
    if action=='ai_answer':return policy=='PERMITTED' and teacher_approved
    if action=='ai_generated_case':return role=='teacher' and policy!='PROHIBITED' and teacher_approved
    return False
