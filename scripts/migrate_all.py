from app import app, db
from sqlalchemy import inspect, text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def migrate_all():
    with app.app_context():
        inspector = inspect(db.engine)
        
        type_mapping = {
            'INTEGER': 'INTEGER',
            'STRING': 'VARCHAR',
            'FLOAT': 'FLOAT',
            'BOOLEAN': 'BOOLEAN',
            'DATETIME': 'DATETIME',
            'DATE': 'DATE',
            'TEXT': 'TEXT',
            'JSON': 'JSON'
        }

        for table_name in db.metadata.tables.keys():
            if not inspector.has_table(table_name):
                logger.info(f"Table {table_name} does not exist. Creating it.")
                db.metadata.tables[table_name].create(db.engine)
                continue

            existing_columns = [col['name'] for col in inspector.get_columns(table_name)]
            model_columns = db.metadata.tables[table_name].columns

            for col in model_columns:
                if col.name not in existing_columns:
                    col_type = str(col.type).split('(')[0].upper()
                    sqlite_type = type_mapping.get(col_type, 'VARCHAR')
                    
                    default_val = ''
                    if col.default is not None and col.default.arg is not None:
                        if isinstance(col.default.arg, bool):
                            default_val = f" DEFAULT {int(col.default.arg)}"
                        elif isinstance(col.default.arg, (int, float)):
                            default_val = f" DEFAULT {col.default.arg}"
                        elif isinstance(col.default.arg, str):
                            default_val = f" DEFAULT '{col.default.arg}'"
                    elif col.nullable:
                        default_val = " DEFAULT NULL"
                        
                    alter_query = f"ALTER TABLE {table_name} ADD COLUMN {col.name} {sqlite_type}{default_val}"
                    try:
                        logger.info(f"Running: {alter_query}")
                        db.session.execute(text(alter_query))
                        db.session.commit()
                    except Exception as e:
                        logger.error(f"Failed to add column {col.name} to {table_name}: {e}")
                        db.session.rollback()

        logger.info("Dynamic migration completed.")

if __name__ == "__main__":
    migrate_all()
