# Recipe: Add a New Database Field / Model

How to add a new column to an existing model, or create an entirely new model.

---

## Adding a Column to an Existing Model

### 1. Add the field to the model in `app/models.py`

```python
class Video(Base):
    __tablename__ = "videos"

    # ... existing columns ...
    my_new_field: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

### 2. Handle the migration

**For development (reset DB):**
Delete `data/ytdl-stash.db` and restart. `init_db()` recreates all tables from scratch.

**For production (Alembic, if configured):**
```bash
alembic revision --autogenerate -m "add my_new_field to videos"
alembic upgrade head
```

**Without Alembic (manual SQL):**
```sql
ALTER TABLE videos ADD COLUMN my_new_field VARCHAR(255);
```

### 3. Use in queries

```python
video.my_new_field = "some value"
# Commit via get_db dependency
```

---

## Creating a New Model

### 1. Define the model in `app/models.py`

```python
class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

### 2. Follow the conventions

- Always include `id: Mapped[int]` as primary key.
- Always include `created_at` and `updated_at` timestamps.
- Use `Mapped[T]` + `mapped_column()` (SQLAlchemy 2.x style).
- Add indexes for columns you filter on frequently.
- Add relationships with `cascade="all, delete-orphan"` for parent-child relationships.

### 3. The table is auto-created

`init_db()` calls `Base.metadata.create_all()` which creates any tables that don't exist. New models are picked up automatically as long as they inherit from `Base` and are imported before `init_db()` runs.

**Important**: Make sure the model module is imported in `app/database.py` or `app/main.py`:
```python
import app.models  # noqa: F401 — ensures models are registered with Base
```

### 4. Update documentation

- Add the new model to the schema description in `docs/architecture/README.md`.
- If the model represents a new concept, add it to `docs/glossary.md`.

---

## Column Type Reference

| Python Type | SQLAlchemy Column | SQLite Storage |
|-------------|------------------|----------------|
| `str` | `String(length)` | TEXT |
| `int` | `Integer` | INTEGER |
| `bool` | `Boolean` | INTEGER (0/1) |
| `float` | `Float` | REAL |
| `datetime` | `DateTime` | TEXT (ISO format) |
| `date` | `Date` | TEXT (ISO format) |
| `list / dict` | `JSON` | TEXT (JSON string) |
| `str` (long) | `Text` | TEXT |
| `str \| None` | `String, nullable=True` | TEXT or NULL |

---

## Relationship Patterns

### One-to-many (Channel has many Videos):

```python
# In Channel model:
videos: Mapped[list["Video"]] = relationship(back_populates="channel", cascade="all, delete-orphan")

# In Video model:
channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"))
channel: Mapped["Channel"] = relationship(back_populates="videos")
```

### Loading relationships (async-safe):
```python
result = await db.execute(
    select(Channel).options(selectinload(Channel.videos)).where(Channel.id == channel_id)
)
channel = result.scalar_one()
# channel.videos is now loaded and safe to access
```
