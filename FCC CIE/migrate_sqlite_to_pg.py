"""Copy submissions from a SQLite database into the configured PostgreSQL database.

Usage:
    python migrate_sqlite_to_pg.py --source sqlite:///local.db

The destination is read from DATABASE_URL in config.py (or the environment).
"""

import argparse
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import Base, Submission
from config import DATABASE_URL


def copy_submissions(source_url):
    source_engine = create_engine(source_url)
    destination_engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Base.metadata.create_all(destination_engine)

    with Session(source_engine) as source_session, Session(destination_engine) as destination_session:
        rows = source_session.scalars(select(Submission)).all()
        copied = 0
        for row in rows:
            if destination_session.get(Submission, row.id) is not None:
                continue
            destination_session.add(Submission(
                id=row.id or uuid.uuid4(),
                name=row.name,
                email=row.email,
                message=row.message,
                has_upload=row.has_upload,
                upload_key=row.upload_key,
                original_filename=row.original_filename,
                created_at=(row.created_at or datetime.now(timezone.utc)).astimezone(timezone.utc),
            ))
            copied += 1
        destination_session.commit()
    source_engine.dispose()
    destination_engine.dispose()
    return copied, len(rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='sqlite:///local.db', help='Source SQLite SQLAlchemy URL')
    args = parser.parse_args()
    copied, total = copy_submissions(args.source)
    print(f'Copied {copied} of {total} submissions to destination database.')
