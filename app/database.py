from collections.abc import Generator
from typing import Annotated

from fastapi import Depends
from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from .config import settings

engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        project_columns = {
            column["name"] for column in inspect(engine).get_columns("project")
        }
        if "cover_key" not in project_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE project ADD COLUMN cover_key VARCHAR"))


def get_session() -> Generator[Session]:
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
