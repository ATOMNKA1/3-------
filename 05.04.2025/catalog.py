from enum import Enum as PyEnum
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, validator
from sqlalchemy import create_engine, Column, String, Float, Integer, Enum as SQLAlchemyEnum, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime
import qrcode
import io
import re  

app = FastAPI()

DATABASE_URL = "sqlite:///./minerals.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class RarityType(str, PyEnum):
    COMMON = "COMMON"
    UNCOMMON = "UNCOMMON"
    RARE = "RARE"
    EXTREMELY_RARE = "EXTREMELY_RARE"

class MineralDB(Base):
    __tablename__ = "minerals"
    catalog_id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    chemical_formula = Column(String, nullable=False)
    hardness = Column(Float, nullable=False)
    weight_carats = Column(Float, nullable=False)
    rarity = Column(SQLAlchemyEnum(RarityType), nullable=False)
    origin_country = Column(String, nullable=False)
    specimens_count = Column(Integer, nullable=False)

Base.metadata.create_all(bind=engine)

class Mineral(BaseModel):
    catalog_id: str
    name: str
    chemical_formula: str
    hardness: float
    weight_carats: float
    rarity: RarityType
    origin_country: str
    specimens_count: int

    @validator("catalog_id")
    def validator_catalog_id(cls, catalog_id):
        pattern = r'^[A-Z]{2}-\d{4}$'
        if not re.match(pattern, catalog_id):
            raise ValueError("ID каталога должен иметь формат XX-1234")
        return catalog_id

    @validator("name")
    def validator_name(cls, name):
        if len(name.strip()) < 3:
            raise ValueError("Название минерала должно содержать минимум 3 символа")
        return name
    
    @validator("chemical_formula")
    def validator_formula(cls, chemical_formula):
        if not re.search(r'[A-Za-z]', chemical_formula) or not re.search(r'\d', chemical_formula):
            raise ValueError("Химическая формула должна содержать буквы и цифры")
        return chemical_formula
    
    @validator("hardness")
    def validator_hardness(cls, hardness):
        if hardness < 1.0 or hardness > 10.0:
            raise ValueError("Твердость должна быть от 1 до 10")
        return hardness
    
    @validator("weight_carats")
    def validator_weight(cls, weight_carats):
        if weight_carats <= 0:
            raise ValueError("Вес в каратах должен быть положительным")
        return weight_carats
    
    @validator("origin_country")
    def validator_country(cls, origin_country):
        if len(origin_country.strip()) < 2:
            raise ValueError("Название страны должно содержать минимум 2 символа")
        return origin_country
    
    @validator("specimens_count")
    def validator_count(cls, specimens_count):
        if specimens_count < 0:
            raise ValueError("Количество образцов не может быть отрицательным")
        return specimens_count

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Эндпоинт с поиском → фильтрацией → сортировкой → пагинацией
@app.get("/minerals")
def get_minerals(
    catalog_id: str = None,
    name: str = None,
    rarity: RarityType = None,
    origin_country: str = None,
    search: str = None,          # Поиск по всем полям
    sort: str = None,            # Сортировка (например, "hardness" или "-hardness")
    page: int = 1,               # Номер страницы
    per_page: int = 10,          # Записей на странице
    db: Session = Depends(get_db)
):
    query = db.query(MineralDB)

    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            (MineralDB.catalog_id.ilike(search_pattern)) |
            (MineralDB.name.ilike(search_pattern)) |
            (MineralDB.chemical_formula.ilike(search_pattern)) |
            (MineralDB.origin_country.ilike(search_pattern)) |
            (func.cast(MineralDB.hardness, String).ilike(search_pattern)) |
            (func.cast(MineralDB.weight_carats, String).ilike(search_pattern)) |
            (func.cast(MineralDB.specimens_count, String).ilike(search_pattern)) |
            (func.cast(MineralDB.rarity, String).ilike(search_pattern))
        )

    if catalog_id:
        query = query.filter(MineralDB.catalog_id == catalog_id)
    if name:
        query = query.filter(MineralDB.name == name)
    if rarity:
        query = query.filter(MineralDB.rarity == rarity)
    if origin_country:
        query = query.filter(MineralDB.origin_country == origin_country)

    if sort:
        if sort.startswith("-"):
            query = query.order_by(getattr(MineralDB, sort[1:]).desc())
        else:
            query = query.order_by(getattr(MineralDB, sort))

    offset = (page - 1) * per_page
    results = query.offset(offset).limit(per_page).all()

    return {
        "message": f"Найдено {len(results)} минералов на странице {page}",
        "data": results
    }

@app.post("/minerals")
def create_mineral(mineral: Mineral, db: Session = Depends(get_db)):
    db_mineral = db.query(MineralDB).filter(MineralDB.catalog_id == mineral.catalog_id).first()
    if db_mineral:
        raise HTTPException(status_code=400, detail="Минерал с таким ID уже существует")
    db_mineral = MineralDB(**mineral.dict())
    db.add(db_mineral)
    db.commit()
    db.refresh(db_mineral)
    return {"message": "Минерал добавлен", "data": mineral}

@app.put("/minerals/{catalog_id}")
def update_mineral(catalog_id: str, mineral: Mineral, db: Session = Depends(get_db)):
    if catalog_id != mineral.catalog_id:
        raise HTTPException(status_code=400, detail="ID в пути и теле не совпадают")
    db_mineral = db.query(MineralDB).filter(MineralDB.catalog_id == catalog_id).first()
    if not db_mineral:
        raise HTTPException(status_code=404, detail="Минерал не найден")
    for key, value in mineral.dict().items():
        setattr(db_mineral, key, value)
    db.commit()
    db.refresh(db_mineral)
    return {"message": "Минерал обновлён", "data": mineral}

@app.delete("/minerals/{catalog_id}")
def delete_mineral(catalog_id: str, db: Session = Depends(get_db)):
    db_mineral = db.query(MineralDB).filter(MineralDB.catalog_id == catalog_id).first()
    if not db_mineral:
        raise HTTPException(status_code=404, detail="Минерал не найден")
    db.delete(db_mineral)
    db.commit()
    return {"message": "Минерал удалён", "data": {"catalog_id": catalog_id}}

@app.get("/minerals/{catalog_id}/qr")
def get_mineral_qr(catalog_id: str, db: Session = Depends(get_db)):
    db_mineral = db.query(MineralDB).filter(MineralDB.catalog_id == catalog_id).first()
    if not db_mineral:
        raise HTTPException(status_code=404, detail="Минерал не найден")
    mineral_info = (
        f"Catalog ID: {db_mineral.catalog_id}\n"
        f"Name: {db_mineral.name}\n"
        f"Chemical Formula: {db_mineral.chemical_formula}\n"
        f"Hardness: {db_mineral.hardness}\n"
        f"Weight (carats): {db_mineral.weight_carats}\n"
        f"Rarity: {db_mineral.rarity.value}\n"
        f"Origin Country: {db_mineral.origin_country}\n"
        f"Specimens Count: {db_mineral.specimens_count}"
    )
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
    qr.add_data(mineral_info)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")
    img_byte_arr = io.BytesIO()
    qr_img.save(img_byte_arr, format="PNG")
    img_byte_arr.seek(0)
    return StreamingResponse(img_byte_arr, media_type="image/png", headers={"Content-Disposition": f"inline; filename={catalog_id}_qr.png"})