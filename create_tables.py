from app import app, db

print("Database tables on the creation progress.")
with app.app_context():
    db.create_all()
print("Creation progress is successfully done!")