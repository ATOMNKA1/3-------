import csv
from enum import Enum as PyEnum
from datetime import datetime
import io
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, validator
from sqlalchemy import DATETIME, Column, String, create_engine, Enum as SQLAlchemyEnum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import re

app = FastAPI()

DATABASE_URL = "sqlite:///./games.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class RatingType(str, PyEnum):
    PEGI_3 = "PEGI 3"
    PEGI_7 = "PEGI 7"
    PEGI_12 = "PEGI 12"
    PEGI_16 = "PEGI 16"
    PEGI_18 = "PEGI 18"

class GamesDB(Base):
    __tablename__ = "games"
    game_id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    genre = Column(String, nullable=False)
    platform = Column(String, nullable=False)
    release_date = Column(DATETIME, nullable=False)
    rating = Column(SQLAlchemyEnum(RatingType))
    description = Column(String, nullable=False)

Base.metadata.create_all(bind=engine)

class Games(BaseModel):
    game_id: str
    name: str
    genre: str
    platform: str
    release_date: datetime
    rating: RatingType
    description: str

    @validator("game_id")
    def validator_game_id(cls, game_id):
        pattern = r'^[A-Z]{2}-\d{4}$'
        if not re.match(pattern, game_id):
            raise ValueError("ID игры должен иметь формат XX-1234")
        return game_id

    @validator("name")
    def validator_name(cls, name):
        if len(name.strip()) < 3:
            raise ValueError("Название игры должно содержать минимум 3 символа")
        return name
    
    @validator("description")
    def validator_description(cls, description):
        if len(description.strip()) < 20:  # Исправлено на 20 символов, как в условии
            raise ValueError("Описание игры должно содержать минимум 20 символов")
        return description
    
    @validator("genre")
    def validator_genre(cls, genre):
        if len(genre.strip()) < 3:
            raise ValueError("Жанр игры должен содержать минимум 3 символа")
        return genre
    
    @validator("platform")
    def validator_platform(cls, platform):
        if len(platform.strip()) < 3:
            raise ValueError("Платформа игры должна содержать минимум 3 символа")
        return platform
    
    @validator("release_date")
    def validator_release_date(cls, release_date):
        if release_date > datetime.now():
            raise ValueError("Дата релиза не может быть в будущем")
        return release_date

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/games")
def get_games(
    game_id: str = None,
    name: str = None,
    genre: str = None,
    platform: str = None,
    rating: RatingType = None,
    search: str = None,          # Поиск по всем полям
    sort: str = None,            # Сортировка (например, "name" или "-name")
    page: int = 1,               # Номер страницы
    per_page: int = 10,          # Записей на странице
    db: Session = Depends(get_db)
):
    query = db.query(GamesDB)

    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            (GamesDB.game_id.ilike(search_pattern)) |
            (GamesDB.name.ilike(search_pattern)) |
            (GamesDB.genre.ilike(search_pattern)) |
            (GamesDB.platform.ilike(search_pattern)) |
            (GamesDB.description.ilike(search_pattern)) |
            (GamesDB.rating.ilike(search_pattern))
        )

    if game_id:
        query = query.filter(GamesDB.game_id == game_id)
    if name:
        query = query.filter(GamesDB.name == name)
    if genre:
        query = query.filter(GamesDB.genre == genre)
    if platform:
        query = query.filter(GamesDB.platform == platform)
    if rating:
        query = query.filter(GamesDB.rating == rating)

    if sort:
        if sort.startswith("-"):
            query = query.order_by(getattr(GamesDB, sort[1:]).desc())
        else:
            query = query.order_by(getattr(GamesDB, sort))

    offset = (page - 1) * per_page
    results = query.offset(offset).limit(per_page).all()

    return {
        "message": f"Найдено {len(results)} игр на странице {page}",
        "data": results
    }

@app.post("/games")
def create_game(game: Games, db: Session = Depends(get_db)):
    db_game = db.query(GamesDB).filter(GamesDB.game_id == game.game_id).first()
    if db_game:
        raise HTTPException(status_code=400, detail="Игра с таким ID уже существует")
    db_game = GamesDB(**game.dict())
    db.add(db_game)
    db.commit()
    db.refresh(db_game)
    return {"message": "Игра добавлена", "data": game}

@app.put("/games/{game_id}")
def update_game(game_id: str, game: Games, db: Session = Depends(get_db)):
    if game_id != game.game_id:
        raise HTTPException(status_code=400, detail="ID в пути и теле не совпадают")
    db_game = db.query(GamesDB).filter(GamesDB.game_id == game_id).first()
    if not db_game:
        raise HTTPException(status_code=404, detail="Игра не найдена")
    for key, value in game.dict().items():
        setattr(db_game, key, value)
    db.commit()
    db.refresh(db_game)
    return {"message": "Игра обновлена", "data": game}

@app.delete("/games/{game_id}")
def delete_game(game_id: str, db: Session = Depends(get_db)):
    db_game = db.query(GamesDB).filter(GamesDB.game_id == game_id).first()
    if not db_game:
        raise HTTPException(status_code=404, detail="Игра не найдена")
    db.delete(db_game)
    db.commit()
    return {"message": "Игра удалена", "data": {"game_id": game_id}}

from fastapi.responses import StreamingResponse
import io
import csv
import pandas as pd

@app.get("/games/export")
def export_games(format: str, db: Session = Depends(get_db)):
    # Получаем данные из базы
    games = db.query(GamesDB).all()
    
    if format == "csv":
        # Создаем CSV в памяти
        output = io.StringIO()
        writer = csv.writer(output)
        # Заголовки столбцов
        writer.writerow(["game_id", "name", "genre", "platform", "release_date", "rating", "description"])
        # Данные
        for game in games:
            writer.writerow([
                game.game_id,
                game.name,
                game.genre,
                game.platform,
                game.release_date.isoformat(),
                game.rating.value if game.rating else None,
                game.description
            ])
        # Возвращаем CSV как файл
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode()),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment;filename=games.csv"}
        )
    
    elif format == "json":
        # Преобразуем данные в JSON
        data = [
            {
                "game_id": game.game_id,
                "name": game.name,
                "genre": game.genre,
                "platform": game.platform,
                "release_date": game.release_date.isoformat(),
                "rating": game.rating.value if game.rating else None,
                "description": game.description
            }
            for game in games
        ]
        return {"data": data}
    
    elif format == "xlsx":
        # Создаем DataFrame с помощью pandas
        df = pd.DataFrame([
            {
                "game_id": game.game_id,
                "name": game.name,
                "genre": game.genre,
                "platform": game.platform,
                "release_date": game.release_date,
                "rating": game.rating.value if game.rating else None,
                "description": game.description
            }
            for game in games
        ])
        # Сохраняем DataFrame в Excel в памяти
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False)
        output.seek(0)
        # Возвращаем Excel как файл
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment;filename=games.xlsx"}
        )
    
    else:
        raise HTTPException(status_code=400, detail="Неподдерживаемый формат экспорта")