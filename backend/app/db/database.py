from pathlib import Path

from sqlalchemy import URL, create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, PipelineRun, Report, TraceStep, utc_now


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            URL.create("sqlite", database=str(path)),
            connect_args={"check_same_thread": False, "autocommit": False, "timeout": 10},
        )

        @event.listens_for(self.engine, "connect")
        def configure_sqlite(connection, _):
            # PRAGMAs must run outside a transaction when autocommit=False.
            previous = connection.autocommit
            connection.autocommit = True
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
            connection.autocommit = previous

        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)
        self.recover_interrupted_runs()

    def recover_interrupted_runs(self) -> None:
        """Only safe with the documented single process/container ownership model."""
        now = utc_now()
        with self.sessions.begin() as session:
            runs = session.scalars(select(PipelineRun).where(PipelineRun.status == "processing"))
            for run in runs:
                run.status = "failed"
                run.failure_reason = "interrupted"
                run.failure_message = "Processing was interrupted by an application restart."
                run.completed_at = now
                latest_id = session.scalar(
                    select(PipelineRun.id)
                    .where(PipelineRun.report_id == run.report_id)
                    .order_by(PipelineRun.id.desc())
                    .limit(1)
                )
                if latest_id == run.id:
                    report = session.get(Report, run.report_id)
                    report.status = "failed"
                    report.updated_at = now
                steps = session.scalars(
                    select(TraceStep).where(
                        TraceStep.run_id == run.id, TraceStep.status == "processing"
                    )
                )
                for step in steps:
                    step.status = "interrupted"
                    step.completed_at = now
                    # Usage for an interrupted external call may never be reported.
                    run.cost_complete = False

    def close(self) -> None:
        self.engine.dispose()
