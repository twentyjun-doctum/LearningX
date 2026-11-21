"""Mark stored session questions whose coaching was a fallback as unassessed.

Before this change, fallback coaching (no matching approved evidence, model
timeout or failure, guest classes without a model) stored a default level
("clarify" -> shallow or "analyze" -> mid) that was never judged.

- level: from the latest coaching (SessionQuestion.latest_coaching_json)
- first_level: from the first coaching (Question.feedback_json)

Dry run by default; prints counts only, never question text.

    cd backend
    DATABASE_URL=... python -m scripts.mark_unassessed_levels            # report
    DATABASE_URL=... python -m scripts.mark_unassessed_levels --apply    # write
"""
import argparse
import json

from learningx.classroom import UNASSESSED, SessionQuestion
from learningx.storage import Question, SessionLocal


def _validated(raw):
    try:
        return json.loads(raw or "{}").get("mode") == "MODEL_VALIDATED"
    except (TypeError, ValueError):
        return False


def mark(db, apply=False, batch=500):
    report = {"rows": 0, "level_marked": 0, "first_level_marked": 0}
    last_id = ""
    while True:
        rows = (db.query(SessionQuestion, Question.feedback_json)
                .outerjoin(Question, Question.id == SessionQuestion.question_id)
                .filter(SessionQuestion.id > last_id)
                .order_by(SessionQuestion.id).limit(batch).all())
        if not rows:
            break
        for row, first_coaching in rows:
            report["rows"] += 1
            if row.level != UNASSESSED and not _validated(row.latest_coaching_json):
                report["level_marked"] += 1
                row.level = UNASSESSED
            if row.first_level != UNASSESSED and not _validated(first_coaching):
                report["first_level_marked"] += 1
                row.first_level = UNASSESSED
        last_id = rows[-1][0].id  # read before commit/rollback expires the rows
        if apply:
            db.commit()
        else:
            db.rollback()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the changes (default: report only)")
    args = parser.parse_args()
    with SessionLocal() as db:
        report = mark(db, apply=args.apply)
    print(json.dumps({**report, "applied": args.apply}))


if __name__ == "__main__":
    main()
