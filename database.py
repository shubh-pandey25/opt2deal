import os
from dotenv import load_dotenv
import pymysql
from sqlalchemy import create_engine, Column, String, Text, Boolean, Float, Integer, ForeignKey, DateTime, JSON, func, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from contextlib import contextmanager
import datetime

load_dotenv()

# MySQL Configuration Parameters
host = os.getenv("DB_HOST", "localhost")
user = os.getenv("DB_USER", "root")
password = os.getenv("DB_PASSWORD")

if password is None:
    raise ValueError("DB_PASSWORD environment variable is missing. It must be explicitly set.")

db_name = os.getenv("DB_NAME", "buyer_db")
try:
    port = int(os.getenv("DB_PORT", "3306"))
except ValueError:
    port = 3306

# Connection String & Engine Setup
DATABASE_URL = f"mysql+pymysql://{user}:{password}@{host}:{port}/{db_name}"
engine = create_engine(
    DATABASE_URL, 
    pool_size=10, 
    max_overflow=20, 
    pool_recycle=3600
)

db_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Company(Base):
    __tablename__ = 'companies'
    company_id = Column(Integer, primary_key=True, autoincrement=True)
    company_name = Column(String(255), nullable=False)
    website = Column(String(255), default='N/A')
    
    # Metadata and scraping compatibility fields
    cin_number = Column(String(50), unique=True, nullable=True)
    registration_date = Column(String(50))
    registered_office_address = Column(Text)
    mca_status = Column(String(50))
    state_code = Column(String(50))
    canonical_url = Column(String(255), default='N/A')
    company_description = Column(Text)
    emails = Column(JSON)
    phones = Column(JSON)
    addresses = Column(JSON)
    offerings = Column(JSON)
    crawl_status = Column(String(50), default='pending')
    scraped_at = Column(String(100))
    search_snippets = Column(Text)
    is_pure_software_only = Column(Boolean, default=None)
    is_hardware_related = Column(Boolean, default=None)


class Component(Base):
    __tablename__ = 'components'
    component_id = Column(String(100), primary_key=True)
    component_type = Column(String(100))
    manufacturer = Column(String(100))


class CompanyHsnJunction(Base):
    __tablename__ = 'company_hsn_junction'
    company_id = Column(Integer, ForeignKey('companies.company_id', ondelete='CASCADE'), primary_key=True)
    product_hsn = Column(String(8), primary_key=True)


class CompanyNicJunction(Base):
    __tablename__ = 'company_nic_junction'
    company_id = Column(Integer, ForeignKey('companies.company_id', ondelete='CASCADE'), primary_key=True)
    buyer_industry_code = Column(String(20), primary_key=True)


class ComponentAnalysis(Base):
    __tablename__ = 'component_analyses'
    id = Column(String(50), primary_key=True)
    component_name = Column(String(255), nullable=False)
    part_number = Column(String(100))
    manufacturer = Column(String(100))
    component_type = Column(String(100))
    specs = Column(JSON)
    applications = Column(JSON)
    report = Column(MEDIUMTEXT)
    qa_notes = Column(Text)
    analyzed_at = Column(DateTime, default=func.now())


class ComponentMatch(Base):
    __tablename__ = 'component_matches'
    match_id = Column(Integer, primary_key=True, autoincrement=True)
    analysis_id = Column(String(50), ForeignKey('component_analyses.id', ondelete='CASCADE'))
    company_id = Column(Integer, ForeignKey('companies.company_id', ondelete='CASCADE'))
    match_score = Column(Float, default=1.0)
    status = Column(String(50), default='uncontacted')
    matched_at = Column(DateTime, default=func.now())


class ComponentTrader(Base):
    __tablename__ = 'component_traders'
    trader_id = Column(Integer, primary_key=True, autoincrement=True)
    trader_name = Column(String(255), unique=True)
    website = Column(String(255))
    phone = Column(String(100))
    email = Column(String(255))
    trader_type = Column(String(100), default='Independent Broker')
    last_inventory_sync = Column(DateTime, server_default=func.now(), onupdate=func.now())


class TraderInventoryJunction(Base):
    __tablename__ = 'trader_inventory_junction'
    trader_id = Column(Integer, ForeignKey('component_traders.trader_id', ondelete='CASCADE'), primary_key=True)
    component_part_number = Column(String(100), primary_key=True)
    global_hsn_code = Column(String(10))


def init_db(drop_all=False):
    """Initialize the database and tables."""
    # Ensure target database exists
    temp_conn = pymysql.connect(
        host=host,
        user=user,
        password=password,
        port=port,
        autocommit=True
    )
    with temp_conn.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_name}")
    temp_conn.close()

    # Drop old tables if they exist to avoid schema and foreign key mismatch conflicts
    if drop_all:
        with engine.connect() as conn:
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
            conn.execute(text("DROP TABLE IF EXISTS component_matches;"))
            conn.execute(text("DROP TABLE IF EXISTS company_hsn_junction;"))
            conn.execute(text("DROP TABLE IF EXISTS company_nic_junction;"))
            conn.execute(text("DROP TABLE IF EXISTS companies;"))
            conn.execute(text("DROP TABLE IF EXISTS leads;"))
            conn.execute(text("DROP TABLE IF EXISTS components;"))
            conn.execute(text("DROP TABLE IF EXISTS trader_inventory_junction;"))
            conn.execute(text("DROP TABLE IF EXISTS component_traders;"))
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))
            conn.commit()

    # Run Declarative base migrations
    Base.metadata.create_all(engine)


@contextmanager
def get_session():
    """Provides a transactional scope around a series of operations."""
    session = db_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
